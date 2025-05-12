from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv
import os
import json
import logging
import re
import uuid
import fitz  # PyMuPDF
import shutil
from werkzeug.utils import secure_filename
from datetime import datetime

# Set up logging
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(log_dir, 'prompt_errors.log'),
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables and configure API
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    logging.error("GOOGLE_API_KEY not found in .env file")
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

# Ensure quizzes directory exists
quizzes_dir = os.path.join(os.path.dirname(__file__), 'quizzes')
os.makedirs(quizzes_dir, exist_ok=True)

# Ensure uploads directory exists
uploads_dir = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(uploads_dir, exist_ok=True)

# Flask app
app = Flask(__name__)
CORS(app)

@app.route('/')
def root():
    return send_file('index.html')

def extract_json_from_text(text):
    """Extract JSON array from text, handling various formatting issues."""
    # Try to find JSON array pattern with square brackets
    json_pattern = r'\[\s*\{.*\}\s*\]'
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        return match.group(0)
    
    # If no match, look for the outermost square brackets
    start = text.find('[')
    end = text.rfind(']') + 1
    if start != -1 and end > start:
        return text[start:end]
    
    return None

def fix_json_format(json_str):
    """Fix common JSON formatting issues in AI responses."""
    # Replace incorrect quote characters
    json_str = json_str.replace('"', '"').replace('"', '"')
    json_str = json_str.replace(''', "'").replace(''', "'")
    
    # Fix missing quotes around keys
    json_str = re.sub(r'(\{|\,)\s*([a-zA-Z0-9_]+)\s*:', r'\1 "\2":', json_str)
    
    # Fix trailing commas in arrays/objects
    json_str = re.sub(r',\s*(\}|\])', r'\1', json_str)
    
    return json_str

def validate_and_fix_quiz(quiz):
    """Validate quiz structure and fix common issues."""
    if not isinstance(quiz, list):
        logging.warning("Response is not a list")
        return None
    
    valid_questions = []
    
    for q in quiz:
        if not isinstance(q, dict):
            continue
            
        # Check for required fields
        if not all(k in q for k in ["question", "options", "correct"]):
            # Try to fix missing fields
            if "question" not in q:
                continue  # Skip if no question
            
            if "options" not in q and "choices" in q:
                q["options"] = q["choices"]  # Fix different field name
            
            if "options" not in q or not isinstance(q["options"], list):
                continue  # Skip if can't fix options
                
            if "correct" not in q and "answer" in q:
                q["correct"] = q["answer"]  # Fix different field name
                
            if "correct" not in q and "correctAnswer" in q:
                q["correct"] = q["correctAnswer"]
                
            # Still missing required fields after fixes
            if not all(k in q for k in ["question", "options", "correct"]):
                continue
        
        # Ensure options is a list with at least 2 options
        if not isinstance(q["options"], list) or len(q["options"]) < 2:
            continue
            
        # Add more options if less than 4
        while len(q["options"]) < 4:
            q["options"].append(f"Option {len(q['options']) + 1}")
            
        # Trim options if more than 4
        if len(q["options"]) > 4:
            q["options"] = q["options"][:4]
            
        # Ensure correct answer is in options
        if q["correct"] not in q["options"]:
            q["correct"] = q["options"][0]  # Set first option as correct if needed
            
        valid_questions.append(q)
    
    # Return what we have if at least 1 valid question
    if valid_questions:
        return valid_questions
    return None

@app.route('/generate-quiz', methods=['POST'])
def generate_quiz():
    try:
        # Input validation
        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({"error": "Missing 'text' field"}), 400

        text = data['text'].strip()
        if not text:
            return jsonify({"error": "Text input cannot be empty"}), 400

        # Enhanced prompt with clearer formatting instructions
        prompt = f"""
        Create a quiz with 10 multiple-choice questions {'' if len(text.split()) <= 3 else 'based on this text:'} {text}
        
        Return the quiz in ONLY this JSON format with no other text:
        [
            {{
                "question": "Question text here?",
                "options": ["Option A", "Option B", "Option C", "Option D"],
                "correct": "Option that is correct (must match exactly one item in options array)"
            }},
            ... 9 more similar question objects
        ]
        
        Important rules:
        1. Return EXACTLY 10 questions
        2. Each question MUST have EXACTLY 4 options
        3. The correct answer MUST be one of the options
        4. DO NOT include any markdown formatting, explanations, or any text outside the JSON array
        5. Use simple JSON without any special formatting
        """

        # Set safety settings for more consistent outputs
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]

        # Generate quiz with extended retries
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            try:
                attempts += 1
                response = model.generate_content(
                    prompt, 
                    generation_config={"temperature": 0.2}  # Lower temperature for more consistent formatting
                )
                
                if not response.text:
                    logging.error("Empty response from AI model")
                    if attempts < max_attempts:
                        continue
                    return jsonify({"error": "Empty response from AI model"}), 500

                # Extract JSON from response
                result_text = response.text.strip()
                json_text = extract_json_from_text(result_text)
                
                if not json_text:
                    logging.error(f"Could not find JSON array in response: {result_text[:100]}...")
                    if attempts < max_attempts:
                        continue
                    return jsonify({"error": "Could not find JSON array in response"}), 500

                # Try to fix JSON formatting issues
                json_text = fix_json_format(json_text)
                
                # Parse JSON
                try:
                    quiz = json.loads(json_text)
                except json.JSONDecodeError as e:
                    logging.error(f"JSON decode error: {str(e)} in text: {json_text[:100]}...")
                    if attempts < max_attempts:
                        continue
                    return jsonify({"error": "Failed to parse AI response as JSON"}), 500
                
                # Validate and fix quiz structure
                validated_quiz = validate_and_fix_quiz(quiz)
                if not validated_quiz:
                    logging.error("Failed to validate quiz structure")
                    if attempts < max_attempts:
                        continue
                    return jsonify({"error": "Invalid quiz structure"}), 500
                
                # Ensure we have exactly 10 questions
                if len(validated_quiz) < 10:
                    # If we don't have enough questions, try again
                    if attempts < max_attempts:
                        continue
                    
                    # On final attempt, pad with generic questions if needed
                    while len(validated_quiz) < 10:
                        validated_quiz.append({
                            "question": f"Additional question about {text}?",
                            "options": ["Option A", "Option B", "Option C", "Option D"],
                            "correct": "Option A"
                        })
                elif len(validated_quiz) > 10:
                    # Trim excess questions
                    validated_quiz = validated_quiz[:10]
                
                # Save quiz to file
                try:
                    quiz_path = os.path.join(quizzes_dir, 'generated_quiz.json')
                    with open(quiz_path, 'w') as f:
                        json.dump(validated_quiz, f, indent=2)
                except Exception as e:
                    logging.error(f"Failed to save quiz to file: {str(e)}")
                    # Continue even if saving fails
                
                return jsonify(validated_quiz)

            except Exception as e:
                logging.error(f"Attempt {attempts} error: {str(e)}")
                if attempts >= max_attempts:
                    return jsonify({"error": "Failed to generate quiz after multiple attempts"}), 500
                # Continue to next attempt

    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500

@app.route('/api/generate-quiz-from-pdf', methods=['POST'])
def generate_quiz_from_pdf():
    try:
        # Check if the request has a file part
        if 'pdf' not in request.files:
            return jsonify({"error": "No PDF file provided"}), 400
            
        pdf_file = request.files['pdf']
        
        # If user does not select file, browser might submit an empty file
        if pdf_file.filename == '':
            return jsonify({"error": "No PDF file selected"}), 400
            
        # Check if it's a PDF file
        if not pdf_file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "Uploaded file is not a PDF"}), 400
            
        # Get additional topic context if provided
        topic_context = request.form.get('topic', '')
        
        # Create a unique filename to avoid collisions
        unique_filename = f"{uuid.uuid4().hex}_{secure_filename(pdf_file.filename)}"
        pdf_path = os.path.join(uploads_dir, unique_filename)
        
        try:
            # Save the file temporarily
            pdf_file.save(pdf_path)
            
            # Check file size (max 10MB)
            if os.path.getsize(pdf_path) > 10 * 1024 * 1024:
                os.remove(pdf_path)  # Clean up
                return jsonify({"error": "PDF file size exceeds 10MB limit"}), 400
                
            # Extract text from PDF
            extracted_text = ""
            try:
                doc = fitz.open(pdf_path)
                for page in doc:
                    extracted_text += page.get_text()
                doc.close()
            except Exception as e:
                logging.error(f"PDF extraction error: {str(e)}")
                return jsonify({"error": "Failed to extract text from PDF"}), 400
                
            # Check if any text was extracted
            if not extracted_text.strip():
                return jsonify({"error": "No text found in PDF, please upload a text-based PDF"}), 400
                
            # Limit text to 5000 characters
            extracted_text = extracted_text[:5000]
            
            # Prepare prompt for Gemini API
            prompt = f"""
            Create a quiz with 10 multiple-choice questions based on this text extracted from a PDF:
            {extracted_text}
            
            {f'Additional context: {topic_context}' if topic_context else ''}
            
            Return the quiz in ONLY this JSON format with no other text:
            [
                {{
                    "question": "Question text here?",
                    "options": ["Option A", "Option B", "Option C", "Option D"],
                    "correct": "Option that is correct (must match exactly one item in options array)"  
                }},
                ... 9 more similar question objects
            ]
            
            Important rules:
            1. Return EXACTLY 10 questions
            2. Each question MUST have EXACTLY 4 options
            3. The correct answer MUST be one of the options
            4. DO NOT include any markdown formatting, explanations, or any text outside the JSON array
            5. Use simple JSON without any special formatting
            """
            
            # Generate quiz with extended retries
            attempts = 0
            max_attempts = 3
            while attempts < max_attempts:
                try:
                    attempts += 1
                    response = model.generate_content(
                        prompt, 
                        generation_config={"temperature": 0.2}  # Lower temperature for more consistent formatting
                    )
                    
                    if not response.text:
                        logging.error("Empty response from AI model")
                        if attempts < max_attempts:
                            continue
                        return jsonify({"error": "Empty response from AI model"}), 500

                    # Extract JSON from response
                    result_text = response.text.strip()
                    json_text = extract_json_from_text(result_text)
                    
                    if not json_text:
                        logging.error(f"Could not find JSON array in response: {result_text[:100]}...")
                        if attempts < max_attempts:
                            continue
                        return jsonify({"error": "Could not find JSON array in response"}), 500

                    # Try to fix JSON formatting issues
                    json_text = fix_json_format(json_text)
                    
                    # Parse JSON
                    try:
                        quiz = json.loads(json_text)
                    except json.JSONDecodeError as e:
                        logging.error(f"JSON decode error: {str(e)} in text: {json_text[:100]}...")
                        if attempts < max_attempts:
                            continue
                        return jsonify({"error": "Failed to parse AI response as JSON"}), 500
                    
                    # Validate and fix quiz structure
                    validated_quiz = validate_and_fix_quiz(quiz)
                    if not validated_quiz:
                        logging.error("Failed to validate quiz structure")
                        if attempts < max_attempts:
                            continue
                        return jsonify({"error": "Invalid quiz structure"}), 500
                    
                    # Ensure we have exactly 10 questions
                    if len(validated_quiz) < 10:
                        # If we don't have enough questions, try again
                        if attempts < max_attempts:
                            continue
                        
                        # On final attempt, pad with generic questions if needed
                        while len(validated_quiz) < 10:
                            validated_quiz.append({
                                "question": f"Additional question about the PDF content?",
                                "options": ["Option A", "Option B", "Option C", "Option D"],
                                "correct": "Option A"
                            })
                    elif len(validated_quiz) > 10:
                        # Trim excess questions
                        validated_quiz = validated_quiz[:10]
                    
                    # Save quiz to file
                    try:
                        quiz_path = os.path.join(quizzes_dir, 'generated_quiz.json')
                        with open(quiz_path, 'w') as f:
                            json.dump(validated_quiz, f, indent=2)
                    except Exception as e:
                        logging.error(f"Failed to save quiz to file: {str(e)}")
                        # Continue even if saving fails
                    
                    return jsonify(validated_quiz)

                except Exception as e:
                    logging.error(f"Attempt {attempts} error: {str(e)}")
                    if attempts >= max_attempts:
                        return jsonify({"error": "Failed to generate quiz after multiple attempts"}), 500
                    # Continue to next attempt

        except Exception as e:
            logging.error(f"PDF processing error: {str(e)}")
            return jsonify({"error": "Failed to process PDF file"}), 500
        finally:
            # Clean up the temporary file
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except Exception as e:
                    logging.error(f"Failed to remove temporary PDF file: {str(e)}")

    except Exception as e:
        logging.error(f"Unexpected error in PDF processing: {str(e)}")
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)