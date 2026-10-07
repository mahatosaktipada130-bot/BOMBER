import base64
import json
import os
import re
from threading import Thread
from urllib.parse import parse_qs, urlparse
import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- Dummy Flask Server (Render ke port requirement ke liye) ---
app_flask = Flask('')


@app_flask.route('/')
def home():
  return 'Bot is alive and running!'


def run_flask():
  app_flask.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))


def keep_alive():
  t = Thread(target=run_flask)
  t.start()


# -------------------------------------------------------------

BOT_TOKEN = os.environ.get('BOT_TOKEN')


def decode_single_url(text):
  try:
    parsed_url = urlparse(text.strip())
    query_params = parse_qs(parsed_url.query)

    encoded_data = None
    param_type = 'json'

    if 'share' in query_params:
      encoded_data = query_params['share'][0]
    elif 'm' in query_params:
      encoded_data = query_params['m'][0]
    elif 's' in query_params:
      encoded_data = query_params['s'][0]
      param_type = 'string'

    if encoded_data:
      encoded_data = encoded_data.replace('-', '+').replace('_', '/')
      padding = len(encoded_data) % 4
      if padding > 0:
        encoded_data += '=' * (4 - padding)

      decoded_bytes = base64.b64decode(encoded_data)
      decoded_str = decoded_bytes.decode('utf-8', errors='ignore')

      if param_type == 'string':
        if '|||' in decoded_str:
          split_urls = decoded_str.split('|||')
          return [u.strip() for u in split_urls if u.strip()]
        return [decoded_str]

      try:
        parsed_json = json.loads(decoded_str)
        if isinstance(parsed_json, dict):
          url_val = parsed_json.get('url', '')
          if '|||' in url_val:
            sub_urls = url_val.split('|||')
            return [sub_u.strip() for sub_u in sub_urls if sub_u.strip()]
          return [url_val] if url_val else []
        elif isinstance(parsed_json, list):
          return [
              item.get('url')
              for item in parsed_json
              if isinstance(item, dict) and item.get('url')
          ]
      except:
        if '|||' in decoded_str:
          split_urls = decoded_str.split('|||')
          return [u.strip() for u in split_urls if u.strip()]
        return [decoded_str]

  except Exception as e:
    print(f'Error decoding: {e}')
    return None
  return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      '⚡ **Firebase Scanner Bot Active!**\n\n'
      'Koi bhi encoded link ya Firebase URL bhejo, main seedha Online & Offline devices check karke bhej dunga.',
      parse_mode='Markdown',
  )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
  text = update.message.text.strip()
  lines = text.splitlines()

  urls_to_check = []

  # 1. Links ko decode karke Firebase URLs nikalna
  for line in lines:
    if 'http' in line:
      decoded_urls = decode_single_url(line)
      if decoded_urls:
        if isinstance(decoded_urls, list):
          urls_to_check.extend(decoded_urls)
        else:
          urls_to_check.append(decoded_urls)

  if not urls_to_check:
    decoded_urls = decode_single_url(text)
    if decoded_urls:
      if isinstance(decoded_urls, list):
        urls_to_check.extend(decoded_urls)
      else:
        urls_to_check.append(decoded_urls)

  # 2. Agar direct text mein Firebase URL ho toh wo bhi utha lo
  firebase_pattern = r'https?://[a-zA-Z0-9_-]+\.firebaseio\.com'
  found_urls = re.findall(firebase_pattern, text)
  if found_urls:
    urls_to_check.extend(found_urls)

  unique_urls = list(dict.fromkeys(urls_to_check))

  if not unique_urls:
    await update.message.reply_text(
        '❌ Koi valid link ya Firebase URL nahi mila. Dobara check karo bhai!'
    )
    return

  await update.message.reply_text(
      f'⏳ Scanning {len(unique_urls)} Firebase URL(s)...'
  )

  for url in unique_urls:
    base_url = url.strip()
    if not base_url.endswith('.firebaseio.com') and 'firebaseio.com' in base_url:
      match = re.search(
          r'(https?://[a-zA-Z0-9_-]+\.firebaseio\.com)', base_url
      )
      if match:
        base_url = match.group(1)

    online_devices = 0
    offline_devices = 0

    try:
      response = requests.get(f'{base_url}/clients.json', timeout=10)

      if response.status_code == 200:
        data = response.json()

        if data is not None:
          if isinstance(data, dict):
            total_devices = len(data)
            for key, val in data.items():
              if isinstance(val, dict):
                status = str(val.get('status', '')).strip().lower()
                if status in ['online', 'active', 'true', '1']:
                  online_devices += 1
              elif isinstance(val, (str, int, bool)):
                if str(val).strip().lower() in ['online', 'active']:
                  online_devices += 1

            offline_devices = total_devices - online_devices

          elif isinstance(data, list):
            valid_items = [x for x in data if x is not None]
            total_devices = len(valid_items)
            for val in valid_items:
              if isinstance(val, dict):
                status = str(val.get('status', '')).strip().lower()
                if status in ['online', 'active', 'true', '1']:
                  online_devices += 1
            offline_devices = total_devices - online_devices

      report = f'🟢 Online devices     : {online_devices}\n🔴 Offline devices    : {offline_devices}\n\n✅ {base_url}'
      await update.message.reply_text(report)

    except Exception as e:
      error_report = f'❌ Scan Failed or Database Closed!\nError: {str(e)}\n\n🔗 {base_url}'
      await update.message.reply_text(error_report)


def main():
  if not BOT_TOKEN:
    print('❌ Error: BOT_TOKEN is missing!')
    return

  keep_alive()

  app = ApplicationBuilder().token(BOT_TOKEN).build()

  app.add_handler(CommandHandler('start', start))
  app.add_handler(
      MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
  )

  print('🤖 Bot is running on Render...')
  app.run_polling()


if __name__ == '__main__':
  main()
