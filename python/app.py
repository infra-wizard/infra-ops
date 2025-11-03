from flask import Flask, render_template_string
import os

app = Flask(__name__)

# HTML template with modern UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flask Kubernetes App</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 40px;
            max-width: 600px;
            width: 100%;
            text-align: center;
        }
        
        h1 {
            color: #333;
            margin-bottom: 20px;
            font-size: 2.5em;
        }
        
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 1.1em;
        }
        
        .info-box {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 20px;
            margin: 20px 0;
            text-align: left;
        }
        
        .info-item {
            margin: 10px 0;
            font-size: 1em;
        }
        
        .label {
            font-weight: bold;
            color: #667eea;
            display: inline-block;
            min-width: 150px;
        }
        
        .value {
            color: #333;
        }
        
        .status {
            display: inline-block;
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: bold;
            background: #4caf50;
            color: white;
        }
        
        .footer {
            margin-top: 30px;
            color: #999;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Flask Kubernetes App</h1>
        <p class="subtitle">Simple Flask application running in Kubernetes</p>
        
        <div class="info-box">
            <div class="info-item">
                <span class="label">Status:</span>
                <span class="status">✓ Running</span>
            </div>
            <div class="info-item">
                <span class="label">Hostname:</span>
                <span class="value">{{ hostname }}</span>
            </div>
            <div class="info-item">
                <span class="label">Python Version:</span>
                <span class="value">{{ python_version }}</span>
            </div>
            <div class="info-item">
                <span class="label">Flask Version:</span>
                <span class="value">{{ flask_version }}</span>
            </div>
        </div>
        
        <p class="footer">Deployed successfully to Kubernetes! 🎉</p>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    import platform
    import flask
    
    hostname = os.environ.get('HOSTNAME', 'localhost')
    python_version = platform.python_version()
    flask_version = flask.__version__
    
    return render_template_string(
        HTML_TEMPLATE,
        hostname=hostname,
        python_version=python_version,
        flask_version=flask_version
    )

@app.route('/health')
def health():
    return {'status': 'healthy'}, 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

