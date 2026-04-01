import logging
import os
import requests
import asyncio
import time
from datetime import datetime
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from flask import Flask
import threading

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ ОШИБКА: нет BOT_TOKEN!")
    exit(1)

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
if not OPENROUTER_API_KEY:
    print("⚠️ Нет OPENROUTER_API_KEY, бот не сможет отвечать")
else:
    print("✅ OpenRouter ключ загружен")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Состояния
TEST, DIALOG = range(2)
TASK_STATE = 3

# Хранилище данных пользователей
user_data = defaultdict(lambda: {
    "name": None,
    "messages_count": 0,
    "last_test_score": None,
    "last_test_date": None,
    "level": 1
})

# ===== ТЕСТ НА ТРЕВОЖНОСТЬ (УЛУЧШЕННЫЙ) =====
GAD7_QUESTIONS = [
    "1️⃣ Чувствуете ли вы напряжение, не можете расслабиться?",
    "2️⃣ Беспокоитесь ли вы по пустякам больше, чем обычно?",
    "3️⃣ Часто ли вы испытываете страх, что случится что-то плохое?",
    "4️⃣ Трудно ли вам заснуть из-за тревожных мыслей?",
    "5️⃣ Бывает ли, что сердце бьётся чаще без видимой причины?",
    "6️⃣ Чувствуете ли вы, что не можете справиться с повседневными задачами?",
    "7️⃣ Бывает ли, что вы избегаете ситуаций из-за страха?",
    "8️⃣ Часто ли вы чувствуете раздражение или злость?",
    "9️⃣ Бывает ли, что вы не можете усидеть на месте?",
    "🔟 Чувствуете ли вы, что всё идёт не так, как хотелось бы?"
]

ANSWERS = [
    ("✅ Совсем нет", 0),
    ("⚠️ Иногда (1-3 дня в неделю)", 1),
    ("😟 Часто (4-6 дней в неделю)", 2),
    ("😰 Почти каждый день", 3)
]

def interpret_gad7(score):
    if score < 8:
        return "🌿 **Низкий уровень тревоги**\nВы в порядке. Поддерживайте режим сна, отдыхайте, занимайтесь приятными делами."
    elif score < 14:
        return "🌤️ **Умеренный уровень тревоги**\nСтоит уделить внимание самочувствию. Попробуй упражнения на дыхание и заземление."
    elif score < 20:
        return "⚠️ **Высокий уровень тревоги**\nРекомендуется обратиться к психологу. Я всегда готов поддержать тебя."
    else:
        return "🆘 **Очень высокий уровень тревоги**\nНастоятельно рекомендую обратиться к специалисту. Позвони по телефону доверия: 8-800-2000-122"

# ===== УПРАЖНЕНИЯ =====
EXERCISES = {
    "breath": {
        "name": "🌬️ Дыхание 4-7-8",
        "desc": "Успокаивает нервную систему",
        "text": "1. Вдохните носом 4 секунды.\n2. Задержите дыхание на 7 секунд.\n3. Медленно выдохните ртом 8 секунд.\nПовторите 3-5 раз."
    },
    "grounding": {
        "name": "🪴 Заземление 5-4-3-2-1",
        "desc": "Возвращает в реальность",
        "text": "Назовите:\n• 5 вещей, которые видите\n• 4 вещи, которых можете коснуться\n• 3 звука, которые слышите\n• 2 запаха, которые чувствуете\n• 1 вкус, который ощущаете"
    },
    "relax": {
        "name": "💆 Прогрессивная релаксация",
        "desc": "Снимает мышечное напряжение",
        "text": "Поочерёдно напрягайте и расслабляйте:\n• кисти рук\n• предплечья\n• плечи\n• шею\n• лицо\n• грудь\n• живот\n• ноги"
    }
}

# ===== ЗАДАНИЯ =====
TASKS = {
    "morning_intention": {
        "name": "Намерение на день",
        "desc": "Осознанное начало дня",
        "questions": [
            "С каким чувством ты просыпаешься?",
            "Какое у тебя намерение на сегодня?",
            "Что одно маленькое действие приблизит тебя к этому?"
        ]
    },
    "body_check": {
        "name": "Сканирование тела",
        "desc": "Почувствуй, где живёт напряжение",
        "questions": [
            "Где сейчас в теле чувствуешь напряжение?",
            "Если бы напряжение имело цвет и форму, какие они?",
            "Что помогает тебе расслабить эту зону?"
        ]
    },
    "thought_recording": {
        "name": "Дневник мыслей",
        "desc": "Запиши и пересмотри свои мысли",
        "questions": [
            "Какая мысль сейчас вызывает дискомфорт?",
            "Какие доказательства есть у этой мысли?",
            "А какие доказательства против неё?"
        ]
    },
    "comfort_place": {
        "name": "Визуализация спокойствия",
        "desc": "Создай внутреннее убежище",
        "questions": [
            "Представь место, где ты чувствуешь безопасность. Какое оно?",
            "Что ты там видишь, слышишь, чувствуешь?",
            "Как меняется твоё состояние?"
        ]
    },
    "small_action": {
        "name": "Один шаг",
        "desc": "Сделай простое дело, которое улучшит состояние",
        "questions": [
            "Что одно простое действие может улучшить твоё самочувствие?",
            "Что мешает его сделать?",
            "Когда ты готов это сделать?"
        ]
    },
    "self_support": {
        "name": "Поддержка себя",
        "desc": "Напиши слова, которые нужно услышать",
        "questions": [
            "Какие слова поддержки ты сейчас хотел(а) бы услышать?",
            "Что бы ты сказал(а) близкому человеку в такой ситуации?",
            "Как ты можешь сказать это себе?"
        ]
    },
    "letting_go_exercise": {
        "name": "Освобождение",
        "desc": "Избавься от груза, который тянешь",
        "questions": [
            "От какой привычной мысли или чувства хочешь освободиться?",
            "Как выглядит эта тяжесть в теле?",
            "Что станет легче, когда ты это отпустишь?"
        ]
    },
    "joy_finding": {
        "name": "Маленькое удовольствие",
        "desc": "Заметь то, что приносит радость",
        "questions": [
            "Какое простое действие или вещь приносят тебе удовольствие?",
            "Когда ты в последний раз это делал(а)?",
            "Когда сделаешь снова?"
        ]
    },
    "perspective_shift": {
        "name": "Смена перспективы",
        "desc": "Посмотри на ситуацию иначе",
        "questions": [
            "Какая ситуация вызывает трудности?",
            "Как бы на это посмотрел(а) мудрый друг?",
            "Что можно сделать по-другому?"
        ]
    },
    "breath_focus": {
        "name": "Дыхательная практика",
        "desc": "Вернись к себе через дыхание",
        "questions": [
            "Сделай три медленных вдоха и выдоха. Что чувствуешь?",
            "Где в теле заметил(а) изменение?",
            "Как часто можешь возвращаться к дыханию сегодня?"
        ]
    }
}

# ===== КРИЗИСНЫЕ КОНТАКТЫ =====
CRISIS_CONTACTS = """
🚨 **Если вам плохо, позвоните:**
📞 8-800-2000-122 (круглосуточно, бесплатно, анонимно)
📞 112 (служба спасения)

Ты не один. Пожалуйста, обратись за помощью. ❤️
"""

# ===== ФУНКЦИЯ ЗАПРОСА К OPENROUTER =====
async def ask_ai(user_message, user_name):
    if not OPENROUTER_API_KEY:
        return "⚠️ Ключ OpenRouter не настроен. Добавь OPENROUTER_API_KEY в переменные окружения."

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/your_bot_username",
        "X-Title": "Psychologist Bot"
    }

    payload = {
        "model": "openrouter/free",
        "messages": [
            {"role": "system", "content": f"Ты эмпатичный психолог. Имя клиента: {user_name}. Отвечай тепло, поддерживающе, задавай уточняющие вопросы. Не давай пустых советов."},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "max_tokens": 500
    }

    for attempt in range(3):
        try:
            response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
            data = response.json()

            if response.status_code == 200:
                return data["choices"][0]["message"]["content"]

            if response.status_code == 429:
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
                continue

            error_detail = data.get("error", {}).get("message", "Неизвестная ошибка")
            return f"❌ Ошибка OpenRouter: {error_detail} (код {response.status_code})"

        except Exception as e:
            if attempt == 2:
                return f"❌ Ошибка при запросе: {e}"
            await asyncio.sleep(1)

    return "❌ Слишком много запросов. Попробуй позже."

# ===== ОБРАБОТЧИКИ КОМАНД =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    user_data[user_id]["name"] = user_name

    keyboard = [
        [KeyboardButton("🧘 Упражнения"), KeyboardButton("📝 Задания")],
        [KeyboardButton("📊 Тест на тревожность"), KeyboardButton("🆘 Помощь")],
        [KeyboardButton("💬 Поговорить")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"👋 Привет, {user_name}!\nЯ бот-психолог. Выбери, что хочешь сделать:",
        reply_markup=reply_markup
    )
    return DIALOG

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 **Список команд:**
/start — Начать работу
/help — Эта справка
/profile — Твой профиль и статистика
/dialog — Перейти в режим диалога с психологом
/tips — Советы для снижения тревожности
/test — Пройти тест на тревожность
/task — Получить задание для саморазвития
/levels — Твой текущий уровень и прогресс
/crisis — Контакты экстренной помощи

Также ты можешь пользоваться кнопками меню.
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data[user_id]
    name = data["name"] or update.effective_user.first_name
    msg_count = data["messages_count"]
    last_test = data["last_test_score"]
    last_test_date = data["last_test_date"]
    level = data["level"]

    profile_text = f"""
👤 **Профиль пользователя**
Имя: {name}
Сообщений отправлено: {msg_count}
Текущий уровень: {level}
"""
    if last_test is not None:
        profile_text += f"Последний тест: {last_test} баллов\nДата: {last_test_date}\n"
    else:
        profile_text += "Тест на тревожность ещё не пройден.\n"
    profile_text += "\nПродолжай общаться, чтобы повышать уровень!"
    await update.message.reply_text(profile_text, parse_mode='Markdown')

async def dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Я слушаю. Расскажи, что тебя беспокоит?")
    return DIALOG

async def tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips_text = """
💡 **Советы для снижения тревожности**

1. **Дыхание** – делай глубокие вдохи и медленные выдохи.
2. **Заземление** – используй технику 5-4-3-2-1.
3. **Движение** – прогулка помогает снять напряжение.
4. **Разговор** – поделись чувствами с близкими или напиши мне.
5. **Ограничь новости** – слишком много информации усиливает тревогу.
6. **Режим сна** – старайся ложиться и вставать в одно время.

Попробуй применить хотя бы один совет сегодня.
    """
    await update.message.reply_text(tips_text, parse_mode='Markdown')

async def levels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    level = user_data[user_id]["level"]
    msg_count = user_data[user_id]["messages_count"]
    next_level = level + 1
    progress = msg_count % 10
    progress_bar = "🟩" * progress + "⬜" * (10 - progress)

    text = f"""
📊 **Твой уровень: {level}**
Сообщений: {msg_count}
Чтобы достичь {next_level} уровня, нужно ещё {10 - progress} сообщений.
Прогресс: {progress_bar}
    """
    await update.message.reply_text(text, parse_mode='Markdown')

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['test_answers'] = []
    context.user_data['test_step'] = 0
    await update.message.reply_text(
        "📊 **Тест на тревожность**\n\n"
        "Оцени, как часто за последние 2 недели тебя беспокоили эти проблемы:\n"
        f"{GAD7_QUESTIONS[0]}\n\n"
        "Выбери вариант ответа:",
        reply_markup=generate_answer_keyboard()
    )
    return TEST

def generate_answer_keyboard():
    keyboard = [
        [InlineKeyboardButton(text, callback_data=f"ans_{score}")]
        for text, score in ANSWERS
    ]
    return InlineKeyboardMarkup(keyboard)

async def test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    step = context.user_data.get('test_step', 0)
    answers = context.user_data.get('test_answers', [])
    score = int(query.data.split('_')[1])
    answers.append(score)
    step += 1
    context.user_data['test_step'] = step
    context.user_data['test_answers'] = answers
    if step < len(GAD7_QUESTIONS):
        await query.edit_message_text(
            f"📊 **Вопрос {step+1}/{len(GAD7_QUESTIONS)}**\n\n{GAD7_QUESTIONS[step]}",
            reply_markup=generate_answer_keyboard()
        )
    else:
        total = sum(answers)
        interpretation = interpret_gad7(total)
        result_text = (
            f"✅ **Тест завершён!**\n\n"
            f"📊 **Сумма баллов:** {total}\n"
            f"🧠 **Результат:** {interpretation}\n\n"
            f"Если тебе нужна поддержка, напиши мне."
        )
        await query.edit_message_text(result_text, parse_mode='Markdown')
        user_id = update.effective_user.id
        user_data[user_id]["last_test_score"] = total
        user_data[user_id]["last_test_date"] = datetime.now().strftime("%d.%m.%Y")
        del context.user_data['test_answers']
        del context.user_data['test_step']
        return DIALOG

async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tasks(update, context)
    return TASK_STATE

async def crisis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(CRISIS_CONTACTS, parse_mode='Markdown')

# ===== ОБРАБОТЧИКИ КНОПОК =====
async def show_exercises(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(ex["name"], callback_data=f"ex_{key}")]
        for key, ex in EXERCISES.items()
    ]
    keyboard.append([InlineKeyboardButton("◀️ В меню", callback_data="menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери упражнение:", reply_markup=reply_markup)

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for key, task in TASKS.items():
        keyboard.append([InlineKeyboardButton(task["name"], callback_data=f"task_start_{key}")])
    keyboard.append([InlineKeyboardButton("◀️ В меню", callback_data="menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "📝 **Задания для саморазвития**\n\nВыбери задание, и я задам тебе несколько вопросов:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало интерактивного задания"""
    query = update.callback_query
    await query.answer()
    
    task_key = query.data.replace("task_start_", "")
    context.user_data['current_task'] = task_key
    context.user_data['task_answers'] = []
    context.user_data['task_step'] = 0
    
    task = TASKS[task_key]
    await query.edit_message_text(
        f"📝 **{task['name']}**\n_{task['desc']}_\n\n"
        f"**Вопрос 1 из {len(task['questions'])}:**\n{task['questions'][0]}",
        parse_mode='Markdown'
    )
    return TASK_STATE

async def handle_task_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответов на вопросы задания с обратной связью"""
    text = update.message.text
    
    current_task = context.user_data.get('current_task')
    if not current_task:
        return DIALOG
    
    step = context.user_data.get('task_step', 0)
    answers = context.user_data.get('task_answers', [])
    task = TASKS[current_task]
    
    answers.append(text)
    context.user_data['task_answers'] = answers
    step += 1
    context.user_data['task_step'] = step
    
    if step < len(task['questions']):
        await update.message.reply_text(
            f"📝 **Вопрос {step+1} из {len(task['questions'])}:**\n{task['questions'][step]}",
            parse_mode='Markdown'
        )
        return TASK_STATE
    else:
        analysis_prompt = f"""
Ты психолог. Пользователь выполнил задание "{task['name']}".

Вот его ответы на вопросы:
{chr(10).join([f"{i+1}. {a}" for i, a in enumerate(answers)])}

Твоя задача:
1. Тепло отреагируй на ответы (поддержи, отметь, что услышал)
2. Дай 1-2 конкретных совета, связанных с этим заданием
3. Если видишь тревожные паттерны — мягко укажи и предложи способ
4. Ответ должен быть коротким (3-5 предложений), но содержательным

Формат ответа: без лишних вступлений, просто текст совета и поддержки.
"""
        
        analysis = await ask_ai(analysis_prompt, update.effective_user.first_name)
        
        task_tips = {
            "morning_intention": "💡 Совет: Утреннее намерение работает лучше, если записать его и перечитать днём. Это возвращает фокус.",
            "body_check": "💡 Совет: Делай сканирование тела 2-3 раза в день — тревога быстрее замечается и отпускается.",
            "thought_recording": "💡 Совет: Когда записываешь тревожную мысль, всегда добавляй вопрос: «А что, если всё пойдёт хорошо?»",
            "comfort_place": "💡 Совет: Практикуй визуализацию каждый день по 1-2 минуты — со временем образ станет очень устойчивым.",
            "small_action": "💡 Совет: Если не можешь сделать даже маленький шаг — сделай ещё меньше. Главное — начать движение.",
            "self_support": "💡 Совет: Запиши эти слова в заметки и перечитывай в трудный момент. Поддержка себя — навык.",
            "letting_go_exercise": "💡 Совет: Отпускание не происходит за раз. Возвращайся к этому упражнению, когда чувствуешь тяжесть.",
            "joy_finding": "💡 Совет: Создай список «маленьких радостей» на случай, когда трудно вспомнить, что тебя радует.",
            "perspective_shift": "💡 Совет: Представь, что ситуация случилась с лучшим другом. Что бы ты ему посоветовал?",
            "breath_focus": "💡 Совет: Поставь напоминание на телефоне «подыши» 2-3 раза в день. Регулярность важнее длительности."
        }
        
        general_tip = task_tips.get(current_task, "💡 Совет: Регулярная практика этих заданий формирует новый, более спокойный способ реагировать на стресс.")
        
        result_text = f"✨ **Задание выполнено!**\n\n{analysis}\n\n{general_tip}\n\nТы молодец. 🌿"
        
        await update.message.reply_text(result_text, parse_mode='Markdown')
        
        del context.user_data['current_task']
        del context.user_data['task_answers']
        del context.user_data['task_step']
        return DIALOG

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "menu":
        keyboard = [
            [KeyboardButton("🧘 Упражнения"), KeyboardButton("📝 Задания")],
            [KeyboardButton("📊 Тест на тревожность"), KeyboardButton("🆘 Помощь")],
            [KeyboardButton("💬 Поговорить")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await query.message.reply_text("Главное меню:", reply_markup=reply_markup)
        return
    
    if data.startswith("ex_"):
        key = data[3:]
        ex = EXERCISES.get(key)
        if ex:
            text = f"**{ex['name']}**\n_{ex['desc']}_\n\n{ex['text']}"
            await query.edit_message_text(text, parse_mode='Markdown')
            kb = [[InlineKeyboardButton("◀️ К упражнениям", callback_data="back_ex")]]
            await query.message.reply_text("Выполни упражнение. Когда захочешь вернуться, нажми кнопку.",
                                           reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data == "back_ex":
        keyboard = [
            [InlineKeyboardButton(ex["name"], callback_data=f"ex_{key}")]
            for key, ex in EXERCISES.items()
        ]
        keyboard.append([InlineKeyboardButton("◀️ В меню", callback_data="menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выбери упражнение:", reply_markup=reply_markup)
        return
    
    if data.startswith("task_start_"):
        return await handle_task_start(update, context)
    
    if data == "back_task":
        await show_tasks_from_callback(query)
        return

async def show_tasks_from_callback(query):
    keyboard = []
    for key, task in TASKS.items():
        keyboard.append([InlineKeyboardButton(task["name"], callback_data=f"task_start_{key}")])
    keyboard.append([InlineKeyboardButton("◀️ В меню", callback_data="menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "📝 **Задания для саморазвития**\n\nВыбери задание:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ===== ОБЩЕНИЕ =====
async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        text = update.message.text
        user_name = update.effective_user.first_name

        user_data[user_id]["messages_count"] += 1
        user_data[user_id]["level"] = (user_data[user_id]["messages_count"] // 10) + 1

        if text == "🧘 Упражнения":
            return await show_exercises(update, context)
        elif text == "📝 Задания":
            return await show_tasks(update, context)
        elif text == "📊 Тест на тревожность":
            return await test_command(update, context)
        elif text == "🆘 Помощь":
            return await crisis(update, context)
        elif text == "💬 Поговорить":
            await update.message.reply_text("Я слушаю. Расскажи, что тебя беспокоит?")
            return

        crisis_words = ["самоубийств", "смерть", "умереть", "покончить", "не хочу жить"]
        if any(word in text.lower() for word in crisis_words):
            await update.message.reply_text(CRISIS_CONTACTS, parse_mode='Markdown')

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        reply = await ask_ai(text, user_name)
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"🔥 Ошибка: {e}")

# ===== RENDER =====
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "🤖 Бот-психолог работает!"

@web_app.route('/health')
def health():
    return "OK", 200

def run_web():
    port = int(os.environ.get('PORT', 5000))
    web_app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web).start()
print("🌐 Веб-сервер запущен")

# ===== MAIN =====
def main():
    try:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        app = Application.builder().token(BOT_TOKEN).build()
        
        test_conv = ConversationHandler(
            entry_points=[
                CommandHandler("test", test_command),
                MessageHandler(filters.Regex("^📊 Тест на тревожность$"), test_command)
            ],
            states={TEST: [CallbackQueryHandler(test_handler, pattern="^ans_")]},
            fallbacks=[CommandHandler("start", start)]
        )
        
        task_conv = ConversationHandler(
            entry_points=[
                CommandHandler("task", task_command),
                MessageHandler(filters.Regex("^📝 Задания$"), task_command),
                CallbackQueryHandler(handle_task_start, pattern="^task_start_")
            ],
            states={
                TASK_STATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task_answer),
                    CallbackQueryHandler(handle_task_start, pattern="^task_start_"),
                    CallbackQueryHandler(show_tasks_from_callback, pattern="^back_task$"),
                    CallbackQueryHandler(button_callback, pattern="^menu$")
                ]
            },
            fallbacks=[CommandHandler("start", start), CommandHandler("menu", start)]
        )
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("profile", profile))
        app.add_handler(CommandHandler("dialog", dialog))
        app.add_handler(CommandHandler("tips", tips))
        app.add_handler(CommandHandler("levels", levels))
        app.add_handler(CommandHandler("crisis", crisis))
        app.add_handler(test_conv)
        app.add_handler(task_conv)
        
        app.add_handler(MessageHandler(filters.Regex("^🧘 Упражнения$"), show_exercises))
        app.add_handler(MessageHandler(filters.Regex("^📝 Задания$"), show_tasks))
        app.add_handler(MessageHandler(filters.Regex("^🆘 Помощь$"), crisis))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, talk))
        app.add_handler(CallbackQueryHandler(button_callback, pattern="^(ex_|back_ex|menu|task_start_|back_task)$"))

        def self_ping():
            url = os.environ.get('RENDER_EXTERNAL_URL', 'https://telegram-6ki9.onrender.com')
            while True:
                time.sleep(300)
                try:
                    requests.get(f"{url}/health", timeout=10)
                    print("📡 Пинг выполнен")
                except Exception as e:
                    print(f"❌ Пинг не удался: {e}")

        ping_thread = threading.Thread(target=self_ping, daemon=True)
        ping_thread.start()
        print("🔄 Автопинг запущен — бот не уснёт")

        print("✅ Бот запущен!")
        app.run_polling()
        
    except Exception as e:
        import traceback
        print("❌ Ошибка в main:")
        traceback.print_exc()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
