import datetime
import os
import re
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# Render ke environment variable se token uthayega
BOT_TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Firebase Checker Bot Ready!\n\n"
        "Mujhe koi bhi text bhejo jisme Firebase URL ho (Single ya Multiple). Main automatically extract karke check kar lunga!"
    )

async def check_firebase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # Regex pattern to find all Firebase Realtime Database URLs in the text
    firebase_pattern = r'https?://[a-zA-Z0-9_-]+\.firebaseio\.com'
    found_urls = re.findall(firebase_pattern, text)
    
    if not found_urls:
        await update.message.reply_text("❌ Kripya koi valid Firebase Realtime Database URL bhejein.")
        return

    unique_urls = list(dict.fromkeys(found_urls))
    await update.message.reply_text(f"⏳ Scanning {len(unique_urls)} Firebase URL(s)...")

    for url in unique_urls:
        online_devices = 0
        offline_devices = 0

        try:
            response = requests.get(f"{url}/clients.json", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data is not None:
                    if isinstance(data, dict):
                        total_devices = len(data)
                        for key, val in data.items():
                            if isinstance(val, dict):
                                status = str(val.get("status", "")).strip().lower()
                                if status in ["online", "active", "true", "1"]:
                                    online_devices += 1
                            elif isinstance(val, (str, int, bool)):
                                if str(val).strip().lower() in ["online", "active"]:
                                    online_devices += 1
                                    
                        offline_devices = total_devices - online_devices
                        
                    elif isinstance(data, list):
                        valid_items = [x for x in data if x is not None]
                        total_devices = len(valid_items)
                        for val in valid_items:
                            if isinstance(val, dict):
                                status = str(val.get("status", "")).strip().lower()
                                if status in ["online", "active", "true", "1"]:
                                    online_devices += 1
                        offline_devices = total_devices - online_devices

            report = f"🟢 Online devices     : {online_devices}\n🔴 Offline devices    : {offline_devices}\n\n✅ {url}"
            await update.message.reply_text(report)

        except Exception as e:
            error_report = f"❌ Scan Failed or Database Closed!\nError: {str(e)}\n\n🔗 {url}"
            await update.message.reply_text(error_report)

def main():
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN is missing!")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_firebase))

    print("🤖 Bot is running on Render...")
    app.run_polling()

if __name__ == "__main__":
    main()
