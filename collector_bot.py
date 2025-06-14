import logging
import requests
from bs4 import BeautifulSoup
import html
import asyncio
import json
from datetime import datetime, timedelta
import os
import io
import re

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- НАЛАШТУВАННЯ ---
TOKEN = "8002915386:AAF_Ycg6Ao8i8A114Gcs95s0q6yEcINneVA"
CATALOG_URL = "https://coins.bank.gov.ua/catalog.html"
STATS_FILE = "bot_stats.json"
SEEN_PRODUCTS_FILE = "seen_products.json"
ADMIN_ID = 473804834
CHECK_INTERVAL = 3600  # 1 година

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
def load_data(filename, default_data):
    if not os.path.exists(filename): return default_data
    try:
        with open(filename, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default_data

def save_data(data, filename):
    with open(filename, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

def update_user_activity(user):
    stats = load_data(STATS_FILE, {'users': {}, 'checks': [], 'monitoring_chats': []})
    user_id = str(user.id)
    now_iso = datetime.now().isoformat()
    if 'users' not in stats: stats['users'] = {}
    if user_id not in stats['users']:
        stats['users'][user_id] = {'first_name': user.first_name, 'username': user.username, 'first_seen': now_iso}
    stats['users'][user_id]['last_seen'] = now_iso
    save_data(stats, STATS_FILE)

def log_check_activity():
    stats = load_data(STATS_FILE, {'users': {}, 'checks': [], 'monitoring_chats': []})
    if 'checks' not in stats: stats['checks'] = []
    stats['checks'].append(datetime.now().isoformat())
    save_data(stats, STATS_FILE)

# --- ОСНОВНА ЛОГІКА ---
def scrape_product_details(url):
    details = {"price": "Н/Д", "year": "Н/Д", "mintage": "Н/Д", "stock": "Н/Д", "status_text": ""}
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        price_el = soup.find('span', class_='new_price_card_product')
        if price_el: details['price'] = price_el.text.strip()
        
        param_names = soup.find_all('span', class_='product-parameters-name')
        for param in param_names:
            if 'Рік' in param.text:
                year_el = param.find_next_sibling('p', class_='product-parameters-details')
                if year_el: details['year'] = year_el.text.strip()
            if 'Тираж' in param.text:
                mintage_el = param.find_next_sibling('p', class_='product-parameters-details')
                if mintage_el: details['mintage'] = mintage_el.text.strip()
                
        status_el = soup.find('div', class_='product_labels')
        if status_el and status_el.text.strip(): details['status_text'] = status_el.text.strip().upper()
        
        if not details['status_text']:
            for p_tag in soup.find_all('p'):
                if 'На складі залишилося всього' in p_tag.get_text():
                    stock_span = p_tag.find('span', class_='pd_qty')
                    if stock_span: details['stock'] = stock_span.text.strip()
                    break
    except Exception as e:
        logger.error(f"Не вдалося отримати деталі для {url}: {e}")
    return details

def get_all_product_links():
    response = requests.get(CATALOG_URL, headers={'User-Agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(response.text, 'html.parser')
    return [{"name": a.text.strip(), "url": "https://coins.bank.gov.ua" + a['href']} for a in soup.select('div.product a.model_product')]
    # --- ОБРОБНИКИ КОМАНД TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    keyboard = [
        [KeyboardButton("▶️ Перевірити наявність")],
        [KeyboardButton("🔔 Підписатися на сповіщення"), KeyboardButton("🔕 Відписатися від сповіщень")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        'Вітаю! Я бот Collector UA.\n\n'
        '• Натисніть **"Перевірити наявність"** для ручного оновлення.\n'
        '• Натисніть **"Підписатися"**, щоб отримувати автоматичні сповіщення про новинки.\n\n'
        '<b>Команди для адміна:</b>\n/stats, /export_users, /check_updates',
        reply_markup=reply_markup, parse_mode=ParseMode.HTML
    )

async def check_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    log_check_activity()
    chat_id = update.message.chat_id
    await context.bot.send_message(chat_id=chat_id, text="🔍 <b>Зачекайте, перевіряю весь каталог...</b>", parse_mode=ParseMode.HTML)
    try:
        product_links = await asyncio.to_thread(get_all_product_links)
        if not product_links:
            await context.bot.send_message(chat_id=chat_id, text="Помилка: не вдалося завантажити каталог.")
            return

        in_stock_list, coming_soon_list = [], []
        for link_data in product_links:
            details = await asyncio.to_thread(scrape_product_details, link_data['url'])
            if details.get('status_text') == "" and details.get('stock') == "0": continue
            
            details['name'] = link_data['name']
            details['url'] = link_data['url']
            
            if details.get('status_text'):
                emoji = "📆"
                message_part = (f"{emoji} <b><a href='{details['url']}'>{html.escape(details['name'])}</a></b>\n"
                              f"    <b>Рік:</b> {html.escape(details['year'])}\n    <b>Тираж:</b> {html.escape(details['mintage'])}\n"
                              f"    <b>Статус:</b> {html.escape(details['status_text'])}\n    <b>Ціна:</b> {html.escape(details['price'])}")
                coming_soon_list.append(message_part)
            else:
                emoji = "✅"
                message_part = (f"{emoji} <b><a href='{details['url']}'>{html.escape(details['name'])}</a></b>\n"
                              f"    <b>Рік:</b> {html.escape(details['year'])}\n    <b>Тираж:</b> {html.escape(details['mintage'])}\n"
                              f"    <b>Залишилось на складі:</b> {html.escape(details['stock'])} шт.\n    <b>Ціна:</b> {html.escape(details['price'])}")
                in_stock_list.append(message_part)

        if not in_stock_list and not coming_soon_list:
            await context.bot.send_message(chat_id=chat_id, text="ℹ️ Не знайдено товарів у наявності або тих, що очікуються.", parse_mode=ParseMode.HTML)
        
        if in_stock_list:
            header = f"📲 <b>Продукція доступна до замовлення: {len(in_stock_list)} шт.</b>"
            await context.bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)
            for i in range(0, len(in_stock_list), 5):
                batch = in_stock_list[i:i+5]
                await context.bot.send_message(chat_id=chat_id, text="\n\n".join(batch), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        if coming_soon_list:
            header = f"⏳ <b>Продукція яка буде доступна незабаром: {len(coming_soon_list)} шт.</b>"
            await context.bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)
            for product in coming_soon_list: await context.bot.send_message(chat_id=chat_id, text=product, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Помилка в check_inventory: {e}")
        await context.bot.send_message(chat_id=chat_id, text="<b>Помилка:</b> Не вдалося обробити дані.")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user_activity(update.effective_user)
    chat_id = update.effective_chat.id
    stats = load_data(STATS_FILE, {'users': {}, 'monitoring_chats': []})
    if 'monitoring_chats' not in stats: stats['monitoring_chats'] = []
    if chat_id not in stats['monitoring_chats']:
        stats['monitoring_chats'].append(chat_id)
        save_data(stats, STATS_FILE)
        await update.message.reply_text("✅ Ви успішно підписалися!")
    else:
        await update.message.reply_text("☑️ Ви вже підписані.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user_activity(update.effective_user)
    chat_id = update.effective_chat.id
    stats = load_data(STATS_FILE, {'users': {}, 'monitoring_chats': []})
    if 'monitoring_chats' not in stats: stats['monitoring_chats'] = []
    if chat_id in stats['monitoring_chats']:
        stats['monitoring_chats'].remove(chat_id)
        save_data(stats, STATS_FILE)
        await update.message.reply_text("❌ Ви відписалися.")
    else:
        await update.message.reply_text("Ви і не були підписані.")

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID: return
    stats = load_data(STATS_FILE, {'users': {}, 'checks': [], 'monitoring_chats': []})
    now = datetime.now()
    total_users = len(stats.get('users', {}))
    active_today = sum(1 for u in stats.get('users', {}).values() if datetime.fromisoformat(u['last_seen']) > (now - timedelta(days=1)))
    active_7_days = sum(1 for u in stats.get('users', {}).values() if datetime.fromisoformat(u['last_seen']) > (now - timedelta(days=7)))
    new_today = sum(1 for u in stats.get('users', {}).values() if datetime.fromisoformat(u['first_seen']) > (now - timedelta(days=1)))
    new_7_days = sum(1 for u in stats.get('users', {}).values() if datetime.fromisoformat(u['first_seen']) > (now - timedelta(days=7)))
    checks_today = sum(1 for t in stats.get('checks', []) if datetime.fromisoformat(t) > (now - timedelta(days=1)))
    checks_7_days = sum(1 for t in stats.get('checks', []) if datetime.fromisoformat(t) > (now - timedelta(days=7)))
    subscribers_count = len(stats.get('monitoring_chats', []))
    message = (
        f'📊 <b>Статистика бота "Collector UA"</b>\n\n'
        f'• <b>Всього унікальних користувачів:</b> {total_users}\n'
        f'• <b>Підписано на сповіщення:</b> {subscribers_count}\n\n'
        f'• <b>Активних за сьогодні:</b> {active_today}\n'
        f'• <b>Активних за 7 днів:</b> {active_7_days}\n\n'
        f'• <b>Нових за сьогодні:</b> {new_today}\n'
        f'• <b>Нових за 7 днів:</b> {new_7_days}\n\n'
        f'• <b>Ручних перевірок сьогодні:</b> {checks_today}\n'
        f'• <b>Ручних перевірок за 7 днів:</b> {checks_7_days}'
    )
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)

async def export_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Генерую звіт по всім користувачам...")
    stats = load_data(STATS_FILE, {'users': {}})
    users_to_export = stats.get('users', {})
    if not users_to_export: await update.message.reply_text("Список порожній."); return
    sorted_users = sorted(users_to_export.items(), key=lambda item: item[1]['first_seen'])
    header = f"Звіт по всім користувачам (всього: {len(sorted_users)})\n\n"
    report_lines = [header]
    for i, (user_id, data) in enumerate(sorted_users):
        full_name = f"{data.get('first_name', '')} {data.get('last_name', '') or ''}".strip()
        username = f"@{data.get('username')}" if data.get('username') else "N/A"
        first_seen = datetime.fromisoformat(data.get('first_seen')).strftime('%Y-%m-%d %H:%M')
        last_seen = datetime.fromisoformat(data.get('last_seen')).strftime('%Y-%m-%d %H:%M')
        report_lines.append(f"{i+1}. ID: {user_id}\n   Ім'я: {full_name}\n   Юзернейм: {username}\n   Перший візит: {first_seen}\n   Останній візит: {last_seen}\n")
    report_content = "\n".join(report_lines)
    file_in_memory = io.BytesIO(report_content.encode('utf-8'))
    file_in_memory.name = "all_users.txt"
    await update.message.reply_document(document=file_in_memory)

async def check_and_notify_updates(context: ContextTypes.DEFAULT_TYPE, manual_trigger_chat_id: int = None):
    logger.info("Виконую перевірку оновлень...")
    try:
        all_product_links = await asyncio.to_thread(get_all_product_links)
        seen_urls = set(load_data(SEEN_PRODUCTS_FILE, []))
        current_urls = {p['url'] for p in all_product_links}
        new_urls = current_urls - seen_urls
        if not new_urls and not manual_trigger_chat_id:
            logger.info("Нових товарів не знайдено.")
            return

        if new_urls:
            logger.info(f"ЗНАЙДЕНО НОВІ ТОВАРИ: {len(new_urls)}. Генерую звіт...")
            save_data(list(current_urls), SEEN_PRODUCTS_FILE)
        
        coming_soon_products_details = [p for p in [scrape_product_details(link['url']) | link for link in all_product_links] if p.get('status_text')]
        if not coming_soon_products_details:
            if manual_trigger_chat_id: await context.bot.send_message(chat_id=manual_trigger_chat_id, text="ℹ️ Товарів, що очікуються, наразі немає.")
            return

        grouped_by_date, date_pattern = {}, re.compile(r'(\d{1,2}\s+[а-яА-Я]+)')
        for product in coming_soon_products_details:
            match = date_pattern.search(product['status_text'])
            key = f"В продажу з {match.group(1).lower()}" if match else "Незабаром в продажу (без конкретної дати)"
            if key not in grouped_by_date: grouped_by_date[key] = []
            line = (f"📆 <b><a href='{product['url']}'>{html.escape(product['name'])}</a></b>\n"
                    f"    <b>Рік:</b> {html.escape(product['year'])}\n    <b>Тираж:</b> {html.escape(product['mintage'])}\n"
                    f"    <b>Ціна:</b> {html.escape(product['price'])}")
            grouped_by_date[key].append(line)

        final_message = "🔔 <b>УВАГА! Оновлення в інтернет-магазині НБУ:</b>\n\n"
        for key in sorted(grouped_by_date.keys(), key=lambda x: "яяя" if "Незабаром" in x else x):
            final_message += f"<b>{key}:</b>\n" + "\n".join(grouped_by_date[key]) + "\n\n"
        
        chat_ids_to_notify = [manual_trigger_chat_id] if manual_trigger_chat_id else load_data(STATS_FILE, {}).get('monitoring_chats', [])
        logger.info(f"Надсилаю сповіщення {len(chat_ids_to_notify)} підписникам.")
        for chat_id in chat_ids_to_notify:
            try:
                await context.bot.send_message(chat_id=chat_id, text=final_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                await asyncio.sleep(0.1)
            except TelegramError as e: logger.warning(f"Не вдалося надіслати повідомлення {chat_id}: {e}")
    except Exception as e:
        logger.error(f"Критична помилка в check_and_notify_updates: {e}")

async def manual_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Запускаю перевірку вручну...")
    context.application.create_task(check_and_notify_updates(context, manual_trigger_chat_id=update.effective_chat.id))

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("export_users", export_all_users))
    application.add_handler(CommandHandler("check_updates", manual_check_command))
    
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'▶️ Перевірити наявність'), check_inventory))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'🔔 Підписатися на сповіщення'), subscribe))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'🔕 Відписатися від сповіщень'), unsubscribe))
    
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_and_notify_updates, interval=CHECK_INTERVAL, first=10, name="hourly_check")
    
    print("Бот Collector UA запущений...")
    application.run_polling()

if __name__ == "__main__":
    main()