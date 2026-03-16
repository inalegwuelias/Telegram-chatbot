import os
import logging
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
import threading
import subprocess
import signal
import time

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))

# Bot process control
bot_process = None
bot_status = "stopped"
bot_output = []

def capture_bot_output(process):
    """Capture the output from the bot process."""
    global bot_output
    for line in iter(process.stdout.readline, b''):
        decoded_line = line.decode('utf-8').strip()
        logger.info(f"Bot output: {decoded_line}")
        bot_output.append(decoded_line)
        # Keep only the last 100 lines
        if len(bot_output) > 100:
            bot_output.pop(0)

@app.route('/')
def index():
    """Render the home page."""
    secrets_status = {
        "TELEGRAM_TOKEN": bool(os.environ.get("TELEGRAM_TOKEN")),
        "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")),
        "ASSISTANT_ID": bool(os.environ.get("ASSISTANT_ID"))
    }
    
    return render_template('index.html', 
                          bot_status=bot_status,
                          bot_output=bot_output,
                          secrets_status=secrets_status)

@app.route('/start_bot', methods=['POST'])
def start_bot():
    """Start the Telegram bot."""
    global bot_process, bot_status, bot_output
    
    if bot_status == "running":
        flash("Bot is already running!", "warning")
        return redirect(url_for('index'))
    
    try:
        # Clear previous output
        bot_output = []
        
        # Start the bot as a subprocess
        bot_process = subprocess.Popen(
            ['python', 'telegram_bot.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=False
        )
        
        # Start a thread to capture output
        output_thread = threading.Thread(target=capture_bot_output, args=(bot_process,))
        output_thread.daemon = True
        output_thread.start()
        
        bot_status = "running"
        flash("Telegram bot started!", "success")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        flash(f"Failed to start bot: {str(e)}", "danger")
    
    return redirect(url_for('index'))

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    """Stop the Telegram bot."""
    global bot_process, bot_status
    
    if bot_status != "running" or bot_process is None:
        flash("Bot is not running!", "warning")
        return redirect(url_for('index'))
    
    try:
        # Send SIGTERM signal to the process
        bot_process.send_signal(signal.SIGTERM)
        
        # Wait for up to 5 seconds for the process to terminate
        for _ in range(5):
            if bot_process.poll() is not None:
                break
            time.sleep(1)
        
        # If still running, force kill
        if bot_process.poll() is None:
            bot_process.kill()
        
        bot_status = "stopped"
        flash("Telegram bot stopped!", "success")
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        flash(f"Failed to stop bot: {str(e)}", "danger")
    
    return redirect(url_for('index'))

@app.route('/bot_status', methods=['GET'])
def get_bot_status():
    """Return the current bot status and last few lines of output."""
    return jsonify({
        'status': bot_status,
        'output': bot_output[-10:] if bot_output else []
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)