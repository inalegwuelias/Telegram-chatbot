import os
import logging
import re
import time
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatMemberStatus
import openai
import json
from collections import defaultdict

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get tokens from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")  # Your pre-configured OpenAI Assistant ID

# Initialize OpenAI client
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Store violation counts and temporary bans
violation_counts = defaultdict(int)
temp_banned_users = {}  # user_id: unban_time

# Define moderation patterns
HATE_WORDS_PATTERN = re.compile(r'\b(hate|slur|offensive content|racist|sexist)\b', re.IGNORECASE)
SPAM_PATTERN = re.compile(r'(buy now|click here|limited offer|\$\$\$|earn money fast)', re.IGNORECASE)
LINK_PATTERN = re.compile(r'(https?://\S+|www\.\S+|\S+\.\S+/\S+)', re.IGNORECASE)

# File to store violation data
VIOLATIONS_FILE = "violations.json"


def load_violations():
    """Load violation data from file"""
    global violation_counts, temp_banned_users
    try:
        if os.path.exists(VIOLATIONS_FILE):
            with open(VIOLATIONS_FILE, 'r') as f:
                data = json.load(f)
                violation_counts = defaultdict(int, data.get('violation_counts', {}))
                temp_banned_users = {int(k): v for k, v in data.get('temp_banned_users', {}).items()}
    except Exception as e:
        logger.error(f"Error loading violations data: {e}")


def save_violations():
    """Save violation data to file"""
    try:
        data = {
            'violation_counts': dict(violation_counts),
            'temp_banned_users': temp_banned_users
        }
        with open(VIOLATIONS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving violations data: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    await update.message.reply_text(
        "👋 Hello! I'm your group's AI moderator and assistant. "
        "I can answer questions and enforce the group rules."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a help message when the command /help is issued."""
    # Check if this is a private chat or group
    is_private = update.message.chat.type == "private"

    # Basic commands for all users
    help_text = (
        "🤖 *Bot Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/rules - Display group rules\n"
        "/violations - Check your violation count\n\n"
        "📝 *Features:*\n"
        "• Ask me any question and I'll respond using AI\n"
        "• I automatically moderate messages based on group rules\n\n"
        "⚠️ *Group Rules:*\n"
        "1. No spam messages\n"
        "2. No external links\n"
        "3. No hate speech or offensive content\n\n"
        "🚫 *Violation Policy:*\n"
        "• 3 violations: Banned for 24 hours\n"
        "• 5 violations: Permanently banned"
    )

    # Add admin commands if in private chat or user is admin in group
    if is_private:
        admin_text = (
            "\n\n👮‍♂️ *Admin Commands:*\n"
            "/ban @username [reason] - Ban user permanently\n"
            "/tempban @username [hours] [reason] - Ban for specific hours\n"
            "/unban @username - Unban a user\n"
            "/warn @username [reason] - Add violation to user\n"
            "/unwarn @username - Remove one violation\n"
            "/reset @username - Reset all violations\n"
            "/stats - Show group statistics"
        )
        help_text += admin_text
    else:
        # Only check admin status if in a group
        try:
            user_id = update.message.from_user.id
            chat_id = update.message.chat.id

            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            if chat_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                admin_text = (
                    "\n\n👮‍♂️ *Admin Commands:*\n"
                    "/ban @username [reason] - Ban user permanently\n"
                    "/tempban @username [hours] [reason] - Ban for specific hours\n"
                    "/unban @username - Unban a user\n"
                    "/warn @username [reason] - Add violation to user\n"
                    "/unwarn @username - Remove one violation\n"
                    "/reset @username - Reset all violations\n"
                    "/stats - Show group statistics"
                )
                help_text += admin_text
        except Exception as e:
            logger.error(f"Error checking admin status: {e}")

    await update.message.reply_text(help_text, parse_mode="Markdown")


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the group rules when /rules is issued."""
    rules_text = (
        "📜 *Group Rules:*\n\n"
        "1. *No spam messages*\n"
        "   - No repetitive content\n"
        "   - No promotional material\n\n"
        "2. *No external links*\n"
        "   - No URLs or website references\n"
        "   - No social media links\n\n"
        "3. *No hate speech or offensive content*\n"
        "   - Be respectful to all members\n"
        "   - No discriminatory language\n\n"
        "🚫 *Consequences:*\n"
        "• 3 violations: Banned for 24 hours\n"
        "• 5 violations: Permanently banned"
    )
    await update.message.reply_text(rules_text, parse_mode="Markdown")


async def violations_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a user their violation count."""
    user_id = update.message.from_user.id
    count = violation_counts[str(user_id)]

    if count == 0:
        message = "You have no rule violations. Thank you for being a good community member! 👍"
    else:
        warning = ""
        if count >= 3:
            warning = "\n⚠️ Your next violation will result in a 24-hour ban!"
        if count >= 4:
            warning = "\n⚠️ Your next violation will result in a permanent ban!"

        message = f"You currently have {count} rule violation(s).{warning}"

    await update.message.reply_text(message)


async def get_assistant_response(query: str) -> str:
    """Get a response from your pre-configured OpenAI Assistant."""
    try:
        # Create a thread
        thread = client.beta.threads.create()

        # Add the user's message to the thread
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=query
        )

        # Run the Assistant on the thread
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID
        )

        # Wait for the run to complete
        while True:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            if run_status.status == "completed":
                break
            elif run_status.status == "failed":
                return "Sorry, I encountered an error while processing your request."
            elif run_status.status == "expired":
                return "The request took too long to process. Please try again."
            time.sleep(1)

        # Get the messages
        messages = client.beta.threads.messages.list(
            thread_id=thread.id
        )

        # Find the assistant's response (should be the last message)
        for message in messages.data:
            if message.role == "assistant":
                # Access the message content which is now a list of content parts
                for content_part in message.content:
                    if content_part.type == "text":
                        return content_part.text.value

        return "I processed your request but couldn't generate a proper response."
    except Exception as e:
        logger.error(f"Error getting assistant response: {e}")
        return "Sorry, I'm having trouble processing your request right now."


async def check_and_enforce_bans(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for expired temporary bans and update the records."""
    current_time = datetime.now().timestamp()
    users_to_unban = []

    for user_id, unban_time in list(temp_banned_users.items()):
        if current_time >= unban_time:
            users_to_unban.append(user_id)

    for user_id in users_to_unban:
        # Remove from temp ban list but keep violation count
        temp_banned_users.pop(str(user_id), None)

    if users_to_unban:
        save_violations()


async def record_violation(update: Update, context: ContextTypes.DEFAULT_TYPE, violation_type: str) -> bool:
    """Record a user violation and take appropriate action."""
    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Unknown"

    # Increment violation count
    violation_counts[user_id] += 1
    count = violation_counts[user_id]

    # Save updated violation data
    save_violations()

    # Delete the violating message
    try:
        await context.bot.delete_message(
            chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")

    # Determine action based on violation count
    if count >= 5:
        # Permanent ban
        try:
            await context.bot.ban_chat_member(
                chat_id=update.message.chat_id,
                user_id=int(user_id)
            )
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"⛔ @{username} has been permanently banned after 5 violations."
            )
            logger.info(f"User {user_id} permanently banned after 5 violations")
        except Exception as e:
            logger.error(f"Failed to ban user {user_id}: {e}")
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="⚠️ Failed to ban user. Please check if I have admin privileges."
            )
        return True

    elif count >= 3:
        # Temporary 24-hour ban (only if not already banned)
        if user_id not in temp_banned_users:
            try:
                # Ban for 24 hours
                until_date = datetime.now() + timedelta(days=1)
                await context.bot.ban_chat_member(
                    chat_id=update.message.chat_id,
                    user_id=int(user_id),
                    until_date=until_date
                )

                # Record the unban time
                temp_banned_users[user_id] = until_date.timestamp()
                save_violations()

                await context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text=f"⏳ @{username} has been banned for 24 hours after 3 violations."
                )
                logger.info(f"User {user_id} temporarily banned for 24 hours")
            except Exception as e:
                logger.error(f"Failed to temp ban user {user_id}: {e}")
                await context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text="⚠️ Failed to ban user. Please check if I have admin privileges."
                )
        return True
    else:
        # Just a warning
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"⚠️ @{username}, your message was removed for containing {violation_type}. "
                 f"You now have {count} violation(s). "
                 f"At 3 violations you will be banned for 24 hours, and at 5 you will be permanently banned."
        )
        return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages: moderate content and respond to questions."""
    if not update.message or not update.message.text:
        return

    # Skip processing messages from the bot itself
    if update.message.from_user.id == context.bot.id:
        return

    # Check if user is in a group/supergroup
    if update.message.chat.type in ["group", "supergroup"]:
        # Check for rule violations
        text = update.message.text
        
        # Check if user is temporarily banned
        user_id = str(update.message.from_user.id)
        if user_id in temp_banned_users:
            unban_time = temp_banned_users[user_id]
            current_time = datetime.now().timestamp()
            
            if current_time < unban_time:
                # Still banned, delete message
                try:
                    await context.bot.delete_message(
                        chat_id=update.message.chat_id,
                        message_id=update.message.message_id
                    )
                except Exception as e:
                    logger.error(f"Failed to delete message from banned user: {e}")
                return
            else:
                # Ban expired, remove from banned list
                temp_banned_users.pop(user_id)
                save_violations()

        # Check for hate speech
        if HATE_WORDS_PATTERN.search(text):
            await record_violation(update, context, "hate speech")
            return

        # Check for spam
        if SPAM_PATTERN.search(text):
            await record_violation(update, context, "spam")
            return

        # Check for links
        if LINK_PATTERN.search(text):
            await record_violation(update, context, "external links")
            return

    # Process as a question for AI assistant if no violations
    # Only respond if the bot is mentioned or in private chat
    is_private_chat = update.message.chat.type == "private"
    bot_username = context.bot.username
    is_bot_mentioned = f"@{bot_username}" in update.message.text
    
    if is_private_chat or is_bot_mentioned:
        # Remove the bot mention from the text if present
        query = update.message.text.replace(f"@{bot_username}", "").strip()
        
        # Let user know we're processing
        processing_message = await update.message.reply_text("Processing your request...")
        
        # Get response from OpenAI Assistant
        response = await get_assistant_response(query)
        
        # Delete processing message and send the actual response
        try:
            await context.bot.delete_message(
                chat_id=processing_message.chat_id,
                message_id=processing_message.message_id
            )
        except Exception as e:
            logger.error(f"Failed to delete processing message: {e}")
            
        await update.message.reply_text(response)


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user from the group (admin only)."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    # Check if a username was provided
    if not context.args:
        await update.message.reply_text("❌ Please provide a username to ban: /ban @username [reason]")
        return
        
    target_username = context.args[0].replace("@", "")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
    
    # Find user by username
    try:
        # Unfortunately we need to find the user ID from the username
        # This is a limitation as we can't directly get a user ID from a username without context
        # In a real bot, you might cache user IDs or implement a more robust solution
        
        # Notify about the limitation
        await update.message.reply_text(
            f"⚠️ Attempting to ban user @{target_username}.\n"
            "Note: Due to Telegram API limitations, the bot needs to see a recent message from this user to ban them."
        )
        
        # Try to ban by username (this works if the user is in the group's member list)
        try:
            # Get chat administrators to check if target is an admin
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_usernames = [admin.user.username for admin in admins if admin.user.username]
            
            if target_username in admin_usernames:
                await update.message.reply_text("❌ Cannot ban an administrator.")
                return
                
            # Attempt to ban (this works if the API can resolve the username)
            banned = False
            for member in admins:
                if member.user.username == target_username:
                    await context.bot.ban_chat_member(chat_id=chat_id, user_id=member.user.id)
                    banned = True
                    break
                    
            if banned:
                await update.message.reply_text(
                    f"🚫 User @{target_username} has been banned.\nReason: {reason}"
                )
            else:
                await update.message.reply_text(
                    f"❌ Could not find user @{target_username} in the current members list."
                )
        except Exception as e:
            logger.error(f"Error banning user by username: {e}")
            await update.message.reply_text(f"❌ Failed to ban user: {str(e)}")
    except Exception as e:
        logger.error(f"Error in ban command: {e}")
        await update.message.reply_text("❌ An error occurred while trying to ban the user.")


async def tempban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Temporarily ban a user for specified hours."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    # Check if a username and duration were provided
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Please provide a username and duration: /tempban @username [hours] [reason]"
        )
        return
        
    target_username = context.args[0].replace("@", "")
    
    try:
        hours = float(context.args[1])
        if hours <= 0:
            await update.message.reply_text("❌ Duration must be positive.")
            return
    except ValueError:
        await update.message.reply_text("❌ Duration must be a number.")
        return
        
    reason = " ".join(context.args[2:]) if len(context.args) > 2 else "No reason provided"
    
    # Similar limitation as the ban command
    await update.message.reply_text(
        f"⚠️ Attempting to temporarily ban user @{target_username} for {hours} hours.\n"
        "Note: Due to Telegram API limitations, the bot needs to see a recent message from this user to ban them."
    )
    
    try:
        # Get chat administrators to check if target is an admin
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_usernames = [admin.user.username for admin in admins if admin.user.username]
        
        if target_username in admin_usernames:
            await update.message.reply_text("❌ Cannot ban an administrator.")
            return
            
        # Attempt to ban (this works if the API can resolve the username)
        banned = False
        target_user_id = None
        
        for member in admins:
            if member.user.username == target_username:
                target_user_id = member.user.id
                until_date = datetime.now() + timedelta(hours=hours)
                
                await context.bot.ban_chat_member(
                    chat_id=chat_id, 
                    user_id=target_user_id,
                    until_date=until_date
                )
                banned = True
                break
                
        if banned and target_user_id:
            # Record the ban in our system
            unban_time = (datetime.now() + timedelta(hours=hours)).timestamp()
            temp_banned_users[str(target_user_id)] = unban_time
            save_violations()
            
            await update.message.reply_text(
                f"🕒 User @{target_username} has been banned for {hours} hours.\nReason: {reason}"
            )
        else:
            await update.message.reply_text(
                f"❌ Could not find user @{target_username} in the current members list."
            )
    except Exception as e:
        logger.error(f"Error in tempban command: {e}")
        await update.message.reply_text(f"❌ An error occurred while trying to ban the user: {str(e)}")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a user from the group."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    # Check if a username was provided
    if not context.args:
        await update.message.reply_text("❌ Please provide a username to unban: /unban @username")
        return
        
    target_username = context.args[0].replace("@", "")
    
    # Same limitation as ban and tempban
    await update.message.reply_text(
        f"⚠️ Attempting to unban user @{target_username}.\n"
        "Note: Due to Telegram API limitations, the bot may not be able to identify all banned users."
    )
    
    try:
        # Unfortunately, there's no direct way to get banned users list through the Bot API
        # We'll need to rely on our saved data or the admin to provide the correct user ID
        
        # Try to unban by username (this works in some cases)
        try:
            # This is a workaround that might not work in all cases
            # In a real implementation, you might need to keep track of banned users separately
            success = False
            
            # Check if we have this user in our temp_banned_users
            for user_id_str, _ in list(temp_banned_users.items()):
                # We'd need additional mapping from username to user_id which isn't readily available
                # This is a limitation of the bot API
                pass
                
            # Try to unban directly (this sometimes works if the API can resolve the username)
            try:
                # Get chat member by username (limitation: works only for visible users)
                # This is a fictional approach as Telegram Bot API doesn't directly support this
                
                await update.message.reply_text(
                    f"To unban @{target_username}, please use Telegram's interface:\n"
                    "1. Open the group info\n"
                    "2. Go to 'Banned Users'\n"
                    "3. Find the user and unban them\n\n"
                    "Due to Telegram API limitations, bots cannot reliably unban by username."
                )
            except Exception as e:
                logger.error(f"Error unbanning user by username: {e}")
                await update.message.reply_text(
                    "❌ Could not unban by username. Please use Telegram's interface instead."
                )
        except Exception as e:
            logger.error(f"Error in unban attempt: {e}")
            await update.message.reply_text(f"❌ Failed to unban user: {str(e)}")
    except Exception as e:
        logger.error(f"Error in unban command: {e}")
        await update.message.reply_text("❌ An error occurred while trying to unban the user.")


async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually add a violation warning to a user (admin only)."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    # Check if a username was provided
    if not context.args:
        await update.message.reply_text("❌ Please provide a username to warn: /warn @username [reason]")
        return
        
    target_username = context.args[0].replace("@", "")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
    
    # Similar limitation as other commands
    await update.message.reply_text(
        f"⚠️ Attempting to warn user @{target_username}.\n"
        "Note: Due to Telegram API limitations, the bot needs to see a recent message from this user."
    )
    
    try:
        # Get chat members to find the user ID
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_usernames = [admin.user.username for admin in admins if admin.user.username]
        
        if target_username in admin_usernames:
            await update.message.reply_text("❌ Cannot warn an administrator.")
            return
            
        # Find user ID from username
        target_user_id = None
        for member in admins:
            if member.user.username == target_username:
                target_user_id = member.user.id
                break
                
        if target_user_id:
            # Add a violation
            violation_counts[str(target_user_id)] += 1
            count = violation_counts[str(target_user_id)]
            save_violations()
            
            # Notify about the warning
            warning_message = f"⚠️ @{target_username} has been warned by an admin.\nReason: {reason}\n"
            
            # Check if this warning triggers a ban
            if count >= 5:
                # Permanent ban
                try:
                    await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_user_id)
                    warning_message += "\n⛔ This user has reached 5 violations and has been permanently banned."
                except Exception as e:
                    logger.error(f"Failed to ban user after warning: {e}")
                    warning_message += "\n⚠️ Failed to automatically ban user. Please check my admin privileges."
            elif count >= 3:
                # Temporary 24-hour ban
                try:
                    until_date = datetime.now() + timedelta(days=1)
                    await context.bot.ban_chat_member(
                        chat_id=chat_id,
                        user_id=target_user_id,
                        until_date=until_date
                    )
                    temp_banned_users[str(target_user_id)] = until_date.timestamp()
                    save_violations()
                    warning_message += "\n⏳ This user has reached 3 violations and has been banned for 24 hours."
                except Exception as e:
                    logger.error(f"Failed to temp ban user after warning: {e}")
                    warning_message += "\n⚠️ Failed to automatically ban user. Please check my admin privileges."
            else:
                warning_message += f"\nThis user now has {count} violation(s)."
                
            await update.message.reply_text(warning_message)
        else:
            await update.message.reply_text(
                f"❌ Could not find user @{target_username} in the current members list."
            )
    except Exception as e:
        logger.error(f"Error in warn command: {e}")
        await update.message.reply_text(f"❌ An error occurred while trying to warn the user: {str(e)}")


async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove one violation warning from a user (admin only)."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    # Check if a username was provided
    if not context.args:
        await update.message.reply_text("❌ Please provide a username: /unwarn @username")
        return
        
    target_username = context.args[0].replace("@", "")
    
    # Similar limitation as other commands
    await update.message.reply_text(
        f"⚠️ Attempting to remove a warning from user @{target_username}.\n"
        "Note: Due to Telegram API limitations, the bot needs to have this user in its database."
    )
    
    try:
        # Get chat members to find the user ID
        admins = await context.bot.get_chat_administrators(chat_id)
        
        # Find user ID from username
        target_user_id = None
        for member in admins:
            if member.user.username == target_username:
                target_user_id = member.user.id
                break
                
        if target_user_id:
            # Check if user has any violations
            if str(target_user_id) in violation_counts and violation_counts[str(target_user_id)] > 0:
                # Reduce violation count
                violation_counts[str(target_user_id)] -= 1
                new_count = violation_counts[str(target_user_id)]
                save_violations()
                
                await update.message.reply_text(
                    f"✅ One violation has been removed from @{target_username}.\n"
                    f"They now have {new_count} violation(s)."
                )
            else:
                await update.message.reply_text(
                    f"ℹ️ @{target_username} has no violations to remove."
                )
        else:
            await update.message.reply_text(
                f"❌ Could not find user @{target_username} in the current members list."
            )
    except Exception as e:
        logger.error(f"Error in unwarn command: {e}")
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset all violations for a user (admin only)."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    # Check if a username was provided
    if not context.args:
        await update.message.reply_text("❌ Please provide a username: /reset @username")
        return
        
    target_username = context.args[0].replace("@", "")
    
    # Similar limitation as other commands
    await update.message.reply_text(
        f"⚠️ Attempting to reset violations for user @{target_username}.\n"
        "Note: Due to Telegram API limitations, the bot needs to have this user in its database."
    )
    
    try:
        # Get chat members to find the user ID
        admins = await context.bot.get_chat_administrators(chat_id)
        
        # Find user ID from username
        target_user_id = None
        for member in admins:
            if member.user.username == target_username:
                target_user_id = member.user.id
                break
                
        if target_user_id:
            # Reset violations
            if str(target_user_id) in violation_counts:
                # Remove all violations
                violation_counts.pop(str(target_user_id), None)
                # Also remove from temp bans if present
                temp_banned_users.pop(str(target_user_id), None)
                save_violations()
                
                await update.message.reply_text(
                    f"✅ All violations have been reset for @{target_username}."
                )
            else:
                await update.message.reply_text(
                    f"ℹ️ @{target_username} has no recorded violations."
                )
        else:
            await update.message.reply_text(
                f"❌ Could not find user @{target_username} in the current members list."
            )
    except Exception as e:
        logger.error(f"Error in reset command: {e}")
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show group statistics (admin only)."""
    # Check if user has admin privileges
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await update.message.reply_text("❌ This command is for admins only.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Failed to verify admin status.")
        return
        
    try:
        # Get basic group info
        chat = await context.bot.get_chat(chat_id)
        
        # Count total violations
        total_violations = sum(violation_counts.values())
        users_with_violations = len(violation_counts)
        
        # Count temporary banned users
        current_time = datetime.now().timestamp()
        active_temp_bans = sum(1 for unban_time in temp_banned_users.values() if unban_time > current_time)
        
        # Format the stats message
        stats_message = (
            "📊 *Group Statistics*\n\n"
            f"*Group Name:* {chat.title}\n"
            f"*Total Violations:* {total_violations}\n"
            f"*Users with Violations:* {users_with_violations}\n"
            f"*Currently Temp-Banned:* {active_temp_bans}\n\n"
        )
        
        # Add top violators if any exist
        if violation_counts:
            # Sort users by violation count (descending)
            top_violators = sorted(violation_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            
            if top_violators:
                stats_message += "*Top Violators:*\n"
                for user_id_str, count in top_violators:
                    # Try to get username from user ID (this is a limitation)
                    try:
                        user_id = int(user_id_str)
                        # This might not work for all users due to privacy settings
                        user_info = await context.bot.get_chat(user_id)
                        username = user_info.username or f"User ID: {user_id}"
                        stats_message += f"@{username}: {count} violation(s)\n"
                    except Exception:
                        stats_message += f"User ID {user_id_str}: {count} violation(s)\n"
        
        await update.message.reply_text(stats_message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in stats command: {e}")
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")


async def scheduled_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run scheduled tasks like checking for expired bans."""
    await check_and_enforce_bans(context)


def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Load existing violation data
    load_violations()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(CommandHandler("violations", violations_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("tempban", tempban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("unwarn", unwarn_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("stats", stats_command))

    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Add scheduled tasks (check for expired bans every 5 minutes)
    job_queue = application.job_queue
    job_queue.run_repeating(scheduled_job, interval=300, first=10)

    # Start the Bot
    application.run_polling()


if __name__ == '__main__':
    main()
