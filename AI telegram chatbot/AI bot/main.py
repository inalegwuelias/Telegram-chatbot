import os
import logging
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv, set_key, dotenv_values
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

ENV_FILE = os.path.join(os.path.dirname(__file__), '.env')

def capture_bot_output(process):
    """Capture the output from the bot process."""
    global bot_output, bot_status
    for line in iter(process.stdout.readline, b''):
        decoded_line = line.decode('utf-8').strip()
        logger.info(f"Bot output: {decoded_line}")
        bot_output.append(decoded_line)
        if len(bot_output) > 100:
            bot_output.pop(0)
    # Process ended
    bot_status = "stopped"

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

@app.route('/save_config', methods=['POST'])
def save_config():
    """Save API credentials to the .env file."""
    telegram_token = request.form.get('telegram_token', '').strip()
    openai_api_key = request.form.get('openai_api_key', '').strip()
    assistant_id = request.form.get('assistant_id', '').strip()

    # Create .env file if it doesn't exist
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, 'w').close()

    saved = []
    if telegram_token:
        set_key(ENV_FILE, 'TELEGRAM_TOKEN', telegram_token)
        os.environ['TELEGRAM_TOKEN'] = telegram_token
        saved.append('TELEGRAM_TOKEN')
    if openai_api_key:
        set_key(ENV_FILE, 'OPENAI_API_KEY', openai_api_key)
        os.environ['OPENAI_API_KEY'] = openai_api_key
        saved.append('OPENAI_API_KEY')
    if assistant_id:
        set_key(ENV_FILE, 'ASSISTANT_ID', assistant_id)
        os.environ['ASSISTANT_ID'] = assistant_id
        saved.append('ASSISTANT_ID')

    if saved:
        flash(f"Configuration saved: {', '.join(saved)}", "success")
    else:
        flash("No values provided — nothing was saved.", "warning")

    return redirect(url_for('index'))

@app.route('/start_bot', methods=['POST'])
def start_bot():
    """Start the Telegram bot."""
    global bot_process, bot_status, bot_output

    if bot_status == "running":
        flash("Bot is already running!", "warning")
        return redirect(url_for('index'))

    # Check required env vars
    missing = [k for k in ['TELEGRAM_TOKEN', 'OPENAI_API_KEY', 'ASSISTANT_ID'] if not os.environ.get(k)]
    if missing:
        flash(f"Cannot start bot — missing configuration: {', '.join(missing)}", "danger")
        return redirect(url_for('index'))

    try:
        bot_output = []
        bot_dir = os.path.dirname(__file__)
        bot_process = subprocess.Popen(
            ['python3', os.path.join(bot_dir, 'telegram_bot.py')],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=False,
            env=os.environ.copy(),
            cwd=bot_dir
        )
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
        bot_process.send_signal(signal.SIGTERM)
        for _ in range(5):
            if bot_process.poll() is not None:
                break
            time.sleep(1)
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
    """Return the current bot status and recent output lines."""
    return jsonify({
        'status': bot_status,
        'output': bot_output[-50:] if bot_output else []
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
