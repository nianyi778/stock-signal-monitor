from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup

# 底部常驻菜单
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📡 立即扫描", "📋 查看信号"],
        ["📈 我的自选", "➕ 添加股票"],
    ],
    resize_keyboard=True,
)

def watchlist_inline(tickers: list[str]) -> InlineKeyboardMarkup:
    """每个股票一行，带删除按钮"""
    buttons = [[InlineKeyboardButton(f"❌ {t}", callback_data=f"del:{t}")] for t in tickers]
    return InlineKeyboardMarkup(buttons)

def confirm_add_inline(ticker: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认添加", callback_data=f"add:{ticker}"),
        InlineKeyboardButton("❌ 取消", callback_data="cancel"),
    ]])

def signals_inline(tickers: list[str]) -> InlineKeyboardMarkup:
    """信号结果每个 ticker 一个按钮"""
    buttons = [[InlineKeyboardButton(f"📊 {t}", callback_data=f"sig:{t}")] for t in tickers]
    return InlineKeyboardMarkup(buttons)
