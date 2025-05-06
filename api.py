from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv
import os
import json

# Load environment variables and configure API
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

# Flask app
app = Flask(__name__)
CORS(app)

@app.route('/')
def root():
    return jsonify({"message": "QuizCraft API is running!"})

@app.route('/generate-quiz', methods=['POST'])
def generate_quiz():
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({"error": "Missing 'text' field"}), 400

        text = data['text'].strip()
        if not text:
            return jsonify({"error": "Text input cannot be empty"}), 400

        prompt = f"""
        Create a quiz with 10 multiple-choice questions based on this text: {text}
        
        Return ONLY a JSON array with exactly this format:
        [
            {{
                "question": "What is...",
                "options": ["A", "B", "C", "D"],
                "correct": "A"
            }}
        ]
        
        Rules:
        1. Create exactly 10 questions
        2. Each question must have exactly 4 options
        3. One option must be in the options array
        4. Questions should test different aspects
        5. Return ONLY the JSON array, no other text
        """

        response = model.generate_content(prompt)
        
        if not response.text:
            return jsonify({"error": "Empty response from AI model"}), 500

        # Find and extract JSON
        text = response.text.strip()
        if not text.startswith('['):
            start = text.find('[')
            end = text.rfind(']') + 1
            if start == -1 or end == 0:
                return jsonify({"error": "Could not find JSON array in response"}), 500
            text = text[start:end]

        # Parse and validate JSON
        quiz = json.loads(text)
        
        if not isinstance(quiz, list):
            return jsonify({"error": "Response is not a list"}), 500
        
        if len(quiz) != 10:
            return jsonify({"error": f"Expected 10 questions, got {len(quiz)}"}), 500

        # Validate each question
        for i, q in enumerate(quiz):
            if not isinstance(q, dict):
                return jsonify({"error": f"Question {i+1} is not a dictionary"}), 500
            
            if not all(k in q for k in ["question", "options", "correct"]):
                return jsonify({"error": f"Question {i+1} is missing required fields"}), 500
                
            if not isinstance(q["options"], list) or len(q["options"]) != 4:
                return jsonify({"error": f"Question {i+1} must have exactly 4 options"}), 500
                
            if q["correct"] not in q["options"]:
                return jsonify({"error": f"Question {i+1}'s correct answer must be in options"}), 500

        return jsonify(quiz)

    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse AI response as JSON"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
