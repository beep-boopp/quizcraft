# QuizCraft

An AI-powered quiz generation application that creates interactive quizzes based on provided text content using the Gemini API.

## Setup

1. Install Python dependencies:
```bash
pip3 install -r requirements.txt
```

2. Configure environment variables:
   - Rename `.env.example` to `.env`
   - Add your Google API key to the `.env` file:
     ```
     GOOGLE_API_KEY=your-api-key-here
     ```

## Running the Application

1. Start the Flask backend:
```bash
python3 api.py
```

2. The API will be available at `http://localhost:5000`

## API Endpoints

- `GET /`: Health check endpoint
- `POST /generate-quiz`: Generate a quiz from provided text
  - Request body: `{"text": "Your text content here"}`
  - Returns: Array of quiz questions with multiple choice options

## Features

- Generates 10 multiple-choice questions based on input text
- Uses Google's Gemini AI for intelligent question generation
- Each question has 4 options and one correct answer
- Returns quiz data in JSON format for easy integration
