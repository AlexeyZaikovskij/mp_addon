"""UI strings. English is the primary language, Russian the second one."""
from fastapi import Request

from .config import settings

LANGS = ("en", "ru")
LANG_COOKIE = "mpr_lang"

STRINGS: dict[str, dict[str, str]] = {
    "app_title": {"en": "MultiProbe Reports", "ru": "Отчёты MultiProbe"},
    "login_title": {"en": "Sign in with your MultiProbe account", "ru": "Вход под учётной записью MultiProbe"},
    "login": {"en": "Login", "ru": "Логин"},
    "password": {"en": "Password", "ru": "Пароль"},
    "sign_in": {"en": "Sign in", "ru": "Войти"},
    "sign_out": {"en": "Sign out", "ru": "Выйти"},
    "err_wrong_credentials": {"en": "Wrong login or password.", "ru": "Неверный логин или пароль."},
    "err_user_disabled": {"en": "This user is disabled in MultiProbe.", "ru": "Пользователь отключён в MultiProbe."},
    "err_user_banned": {
        "en": "Too many failed attempts. The user is temporarily locked.",
        "ru": "Слишком много неудачных попыток. Пользователь временно заблокирован.",
    },
    "err_attempts_left": {"en": "Attempts left: {n}", "ru": "Осталось попыток: {n}"},
    "err_unreachable": {
        "en": "MultiProbe server is not reachable.",
        "ru": "Сервер MultiProbe недоступен.",
    },
    "err_generic": {"en": "MultiProbe returned an error: {msg}", "ru": "MultiProbe вернул ошибку: {msg}"},
    "time_zone": {"en": "Time zone", "ru": "Часовой пояс"},
    # Wizard
    "step_type": {"en": "Report type", "ru": "Тип отчёта"},
    "type_alarms": {"en": "Alarm history", "ru": "История алармов"},
    "type_alarms_hint": {"en": "Alarms raised on the selected objects", "ru": "Тревоги на выбранных объектах"},
    "type_measures": {"en": "Measurements", "ru": "Измерения"},
    "type_measures_hint": {"en": "Measured values over time", "ru": "Значения измерений во времени"},
    "step_server": {"en": "Server", "ru": "Сервер"},
    "step_objects": {"en": "Monitoring objects", "ru": "Объекты мониторинга"},
    "step_measures": {"en": "Measurements", "ru": "Измерения"},
    "step_period": {"en": "Period", "ru": "Период"},
    "step_options": {"en": "Options", "ru": "Параметры"},
    "loading": {"en": "Loading…", "ru": "Загрузка…"},
    "search": {"en": "Search…", "ru": "Поиск…"},
    "select_server": {"en": "Select a server", "ru": "Выберите сервер"},
    "all_objects": {"en": "All objects", "ru": "Все объекты"},
    "select_all": {"en": "Select all", "ru": "Выбрать все"},
    "expand_all": {"en": "Expand all", "ru": "Развернуть все"},
    "collapse_all": {"en": "Collapse all", "ru": "Свернуть все"},
    "selected": {"en": "Selected", "ru": "Выбрано"},
    "clear": {"en": "Clear", "ru": "Сбросить"},
    "pick_objects_first": {"en": "Select monitoring objects first.", "ru": "Сначала выберите объекты мониторинга."},
    "no_measures": {"en": "The selected objects have no measurements.", "ru": "У выбранных объектов нет измерений."},
    "period_from": {"en": "From", "ru": "С"},
    "period_to": {"en": "To", "ru": "По"},
    "last_hour": {"en": "Last hour", "ru": "Последний час"},
    "last_24h": {"en": "Last 24 h", "ru": "Последние 24 ч"},
    "last_7d": {"en": "Last 7 days", "ru": "Последние 7 дней"},
    "last_30d": {"en": "Last 30 days", "ru": "Последние 30 дней"},
    "with_chart": {"en": "Add a chart", "ru": "Построить график"},
    "step_alarms": {"en": "Alarms", "ru": "Алармы"},
    "no_alarm_types": {"en": "No alarms are defined for the selected objects.", "ru": "Для выбранных объектов алармы не заданы."},
    "group_not_applicable": {
        "en": "No alarms of this priority for the selected objects",
        "ru": "Нет алармов этого приоритета для выбранных объектов",
    },
    "pick_alarms":{"en": "Select at least one alarm.", "ru": "Выберите хотя бы один аларм."},
    "err_period": {"en": "The end of the period must be after its start.", "ru": "Конец периода должен быть позже начала."},
    "generate": {"en": "Generate report", "ru": "Сформировать отчёт"},
    "measures_next_stage": {
        "en": "Measurement reports arrive in the next stage.",
        "ru": "Отчёты по измерениям появятся на следующем этапе.",
    },
    # Report page
    "report_alarms_title": {"en": "Alarm history", "ru": "История алармов"},
    "back_to_wizard": {"en": "New report", "ru": "Новый отчёт"},
    "generating": {"en": "Generating the report…", "ru": "Формирование отчёта…"},
    "events_loaded": {"en": "Events loaded", "ru": "Загружено событий"},
    "report_failed": {"en": "The report could not be generated", "ru": "Не удалось сформировать отчёт"},
    "server": {"en": "Server", "ru": "Сервер"},
    "period": {"en": "Period", "ru": "Период"},
    "objects": {"en": "Objects", "ru": "Объекты"},
    "user": {"en": "User", "ru": "Пользователь"},
    "generated_at": {"en": "Generated", "ru": "Сформирован"},
    "download": {"en": "Download", "ru": "Скачать"},
    "sum_total": {"en": "Events", "ru": "Событий"},
    "sum_active": {"en": "Active now", "ru": "Активны сейчас"},
    "sum_duration": {"en": "Total duration", "ru": "Суммарная длительность"},
    "top_alarms": {"en": "Most frequent alarms", "ru": "Самые частые алармы"},
    "top_objects": {"en": "Objects with most alarms", "ru": "Объекты с наибольшим числом алармов"},
    "all_groups": {"en": "All priorities", "ru": "Все приоритеты"},
    "all_statuses": {"en": "Any status", "ru": "Любой статус"},
    "no_rows": {"en": "No alarms match.", "ru": "Нет подходящих алармов."},
    "page_of": {"en": "{a}–{b} of {n}", "ru": "{a}–{b} из {n}"},
    "prev": {"en": "Previous", "ru": "Назад"},
    "next": {"en": "Next", "ru": "Вперёд"},
    "col_group": {"en": "Priority", "ru": "Приоритет"},
    "col_priority": {"en": "Level", "ru": "Уровень"},
    "col_alarm": {"en": "Alarm", "ru": "Аларм"},
    "col_condition": {"en": "Fire condition", "ru": "Условие срабатывания"},
    "col_stream": {"en": "Stream", "ru": "Поток"},
    "col_object": {"en": "Monitoring object", "ru": "Объект мониторинга"},
    "col_node": {"en": "Node", "ru": "Узел"},
    "col_detected": {"en": "Detected", "ru": "Начало"},
    "col_finished": {"en": "Finished", "ru": "Окончание"},
    "col_duration": {"en": "Duration", "ru": "Длительность"},
    "col_status": {"en": "Status", "ru": "Статус"},
    "col_confirmed": {"en": "Confirmed", "ru": "Квитирован"},
    "status_active": {"en": "Active", "ru": "Активен"},
    "status_finished": {"en": "Finished", "ru": "Завершён"},
    "yes": {"en": "Yes", "ru": "Да"},
    "no": {"en": "No", "ru": "Нет"},
    "err_session": {"en": "Your session has expired. Please sign in again.", "ru": "Сессия истекла. Войдите снова."},
}


def pick_lang(request: Request) -> str:
    cookie = request.cookies.get(LANG_COOKIE)
    if cookie in LANGS:
        return cookie
    accept = request.headers.get("accept-language", "").lower()
    for part in accept.split(","):
        code = part.split(";")[0].strip()[:2]
        if code in LANGS:
            return code
    return settings.default_lang if settings.default_lang in LANGS else "en"


def translator(lang: str):
    def t(key: str, **kwargs) -> str:
        entry = STRINGS.get(key)
        text = entry.get(lang) or entry["en"] if entry else key
        return text.format(**kwargs) if kwargs else text

    return t


def bundle(lang: str) -> dict[str, str]:
    """All strings in one language, for the browser-side script."""
    return {k: v.get(lang) or v["en"] for k, v in STRINGS.items()}
