import logging
import requests
from bs4 import BeautifulSoup
import html
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

# --- НАЛАШТУВАННЯ ---
TOKEN = "8002915386:AAF_Ycg6Ao8i8A114Gcs95s0q6yEcINneVA"
CATALOG_URL = "https://coins.bank.gov.ua/catalog.html" 

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

def scrape_nbu_coins():
    """Версія з сортуванням товарів на дві категорії."""
    logger.info("Завантажую сторінку каталогу...")
    
    try:
        response = requests.get(CATALOG_URL, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        product_cards = soup.find_all('div', class_='product')
        
        if not product_cards:
            return ["Не вдалося знайти картки товарів на сторінці."]

        in_stock_products = []
        coming_soon_products = []
        logger.info(f"Знайдено {len(product_cards)} товарів. Починаю детальний збір та сортування...")

        for i, card in enumerate(product_cards):
            name_element = card.find('a', class_='model_product')
            if not name_element: continue

            name = name_element.text.strip()
            product_url = "https://coins.bank.gov.ua" + name_element['href']
            
            try:
                product_page_response = requests.get(product_url, headers={'User-Agent': 'Mozilla/5.0'})
                product_page_response.raise_for_status()
                product_soup = BeautifulSoup(product_page_response.text, 'html.parser')
                
                price_element = product_soup.find('span', class_='new_price_card_product')
                price = price_element.text.strip() if price_element else "Н/Д"

                year = "Н/Д"
                mintage = "Н/Д"
                param_names = product_soup.find_all('span', class_='product-parameters-name')
                for param in param_names:
                    if 'Рік' in param.text:
                        year_element = param.find_next_sibling('p', class_='product-parameters-details')
                        if year_element: year = year_element.text.strip()
                    if 'Тираж' in param.text:
                        mintage_element = param.find_next_sibling('p', class_='product-parameters-details')
                        if mintage_element: mintage = mintage_element.text.strip()
                
                status_text = ""
                status_element = product_soup.find('div', class_='product_labels')
                if status_element and status_element.text.strip():
                    status_text = status_element.text.strip().upper()

                stock = "Н/Д"
                if not status_text:
                    all_p_tags = product_soup.find_all('p')
                    for p_tag in all_p_tags:
                        if 'На складі залишилося всього' in p_tag.get_text():
                            stock_span_element = p_tag.find('span', class_='pd_qty')
                            if stock_span_element: stock = stock_span_element.text.strip()
                            break
                
                if status_text == "" and stock == "0":
                    logger.info(f"ФІЛЬТР: Пропускаю '{name}', бо його немає в наявності і він не очікується.")
                    continue
                
            except requests.RequestException as e:
                logger.error(f"Помилка при запиті до сторінки товару {name}: {e}")
                continue
            
            if status_text:
                emoji = "📆"
                message_part = (
                    f"{emoji} <b><a href='{product_url}'>{html.escape(name)}</a></b>\n"
                    f"    <b>Рік:</b> {html.escape(year)}\n"
                    f"    <b>Тираж:</b> {html.escape(mintage)}\n"
                    f"    <b>Статус:</b> {html.escape(status_text)}\n"
                    f"    <b>Ціна:</b> {html.escape(price)}"
                )
                coming_soon_products.append(message_part)
            else:
                emoji = "✅"
                message_part = (
                    f"{emoji} <b><a href='{product_url}'>{html.escape(name)}</a></b>\n"
                    f"    <b>Рік:</b> {html.escape(year)}\n"
                    f"    <b>Тираж:</b> {html.escape(mintage)}\n"
                    f"    <b>Залишилось на складі:</b> {html.escape(stock)} шт.\n"
                    f"    <b>Ціна:</b> {html.escape(price)}"
                )
                in_stock_products.append(message_part)
        
        logger.info("Збір та сортування інформації завершено.")
        return {"in_stock": in_stock_products, "coming_soon": coming_soon_products}

    except Exception as e:
        logger.error(f"Сталася помилка при парсингу: {e}")
        return ["<b>Помилка:</b> Не вдалося обробити дані. Спробуйте пізніше."]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник команди /start. Тепер він створює постійну клавіатуру."""
    # Створюємо кнопку, яка буде внизу екрана
    keyboard = [
        [KeyboardButton("▶️ Перевірити наявність на сайті НБУ")]
    ]
    # Створюємо саму клавіатуру
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        'Вітаю в <b>Collector UA</b>!\n\nНатисніть кнопку внизу, щоб отримати актуальний список монет.',
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def check_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ця функція тепер реагує на натискання постійної кнопки."""
    chat_id = update.message.chat_id
    await context.bot.send_message(chat_id=chat_id, text="🔍 <b>Зачекайте, перевіряю, сортую та фільтрую...</b> Це може зайняти до хвилини.", parse_mode=ParseMode.HTML)
    
    results = await asyncio.to_thread(scrape_nbu_coins)
    
    if isinstance(results, dict):
        in_stock_list = results.get("in_stock", [])
        coming_soon_list = results.get("coming_soon", [])

        if not in_stock_list and not coming_soon_list:
            await context.bot.send_message(chat_id=chat_id, text="ℹ️ Не знайдено товарів у наявності або тих, що очікуються.", parse_mode=ParseMode.HTML)
            return

        # --- БЛОК ТОВАРІВ В НАЯВНОСТІ ---
        if in_stock_list:
            header = f"📲 <b>Продукція доступна до замовлення: {len(in_stock_list)} шт.</b>"
            await context.bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)
            message_batch = []
            for product in in_stock_list:
                message_batch.append(product)
                if len(message_batch) >= 5:
                    await context.bot.send_message(chat_id=chat_id, text="\n\n".join(message_batch), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    message_batch = []
            if message_batch:
                await context.bot.send_message(chat_id=chat_id, text="\n\n".join(message_batch), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        # --- БЛОК ТОВАРІВ, ЩО ОЧІКУЮТЬСЯ ---
        if coming_soon_list:
            # Прибираємо рисочки-розділювач
            header = f"⏳ <b>Продукція яка буде доступна незабаром: {len(coming_soon_list)} шт.</b>"
            await context.bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)
            for product in coming_soon_list:
                await context.bot.send_message(chat_id=chat_id, text=product, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await context.bot.send_message(chat_id=chat_id, text=results[0], parse_mode=ParseMode.HTML)

def main() -> None:
    """Основна функція запуску бота з новим обробником."""
    application = Application.builder().token(TOKEN).build()

    # Створюємо обробники для команди /start та для текстового повідомлення з кнопки
    start_handler = CommandHandler("start", start)
    check_handler = MessageHandler(filters.Text(["▶️ Перевірити наявність на сайті НБУ"]), check_inventory)
    
    application.add_handler(start_handler)
    application.add_handler(check_handler)

    print("Бот Collector UA запущений... Натисніть Ctrl+C для зупинки.")
    application.run_polling()

if __name__ == "__main__":
    main()