🛒 AI Shopping Assistant
This project is a web application that processes natural language shopping requests and suggests suitable products. It is developed using the Flask framework and the OpenAI API.

✨ Features
🤖 Natural Language Processing: Automatic parsing of budget, product type, and brand

🔍 Flexible Product Search: Smart search within a budget range of ±5000/2000 TL

💬 AI Descriptions: Persuasive product descriptions with OpenAI GPT-3.5

🔄 Alternative Product Recommendations: Category-based alternatives if the main product is not found

🎨 Modern UI: Responsive design and user-friendly interface

⚡ Fast Response: Instant results with AJAX

📋 Prerequisites
Python 3.8+

Flask

OpenAI Python library

🚀 Installation
Navigate to the project directory:

Bash
cd project
Install the required packages:

Bash
pip install flask openai
Set your OpenAI API key:
Enter your own OpenAI API key in the line client = OpenAI(api_key="YOUR_API_KEY") inside the app.py file.

Run the application:

Bash
python app.py
Open it in your browser:
http://127.0.0.1:5000/

💡 Usage
Type what you want in the text box on the homepage

Click the "Submit" button

See the AI's recommendation!

🔗 API Endpoints
GET /: Homepage

POST /recommend: Product recommendation API

📁 File Structure
project/
├── app.py                 # Main Flask application
├── products.json          # 12+ simulated products
├── requirements.txt       # Python dependencies
├── README.md              # This documentation
└── templates/
    ├── index.html         # Main interface
    └── styles.css         # CSS styles
🎯 Example Usage
✅ Examples Where Products Are Found Directly:
"I want an iPhone with 25000 TL"

"Apple MacBook with 35000 TL budget"

"15000 TL Samsung phone"

🔄 Examples Showing Alternative Products:
"Samsung phone with 5000 TL" → Very low budget, suggest alternatives

"100000 TL computer" → Very high budget, suggest suitable products

⚠️ Notes
This application uses simulated data

Additional developments may be required for real e-commerce integration

OpenAI API usage fees may apply

📄 License
This project is open-source and released under the MIT license.
