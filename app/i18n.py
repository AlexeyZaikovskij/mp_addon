"""UI strings. English is the primary language, Russian the second one."""
from fastapi import Request

from .config import settings

LANGS = ("en", "ru")
LANG_COOKIE = "mpr_lang"

STRINGS: dict[str, dict[str, str]] = {
    "app_title": {"en": "MultiProbe Toolkit", "ru": "MultiProbe Toolkit"},
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
    # Templates
    "nav_overview": {"en": "Overview", "ru": "Обзор"},
    "nav_reports": {"en": "Reports", "ru": "Отчёты"},
    "ov_updated": {"en": "Updated", "ru": "Обновлено"},
    "ov_auto_refresh": {"en": "Auto-refresh (30 s)", "ru": "Автообновление (30 с)"},
    "ov_refresh": {"en": "Refresh", "ru": "Обновить"},
    "ov_state_now": {"en": "Channels right now", "ru": "Состояние каналов сейчас"},
    "ov_state_hint": {
        "en": "A channel takes the state of the most severe alarm active on it or on its PIDs. Click a sector to list those channels.",
        "ru": "Канал получает состояние по самому серьёзному активному аларму на нём или его PID. Клик по сектору — список этих каналов.",
    },
    "ov_channels": {"en": "channels", "ru": "каналов"},
    "ov_state_ok": {"en": "No problems", "ru": "Без проблем"},
    "ov_state_caution": {"en": "Caution", "ru": "Caution"},
    "ov_state_warning": {"en": "Warning", "ru": "Warning"},
    "ov_state_error": {"en": "Error", "ru": "Error"},
    "ov_problems_now": {"en": "Problems now", "ru": "Проблемы сейчас"},
    "ov_show_all_states": {"en": "Show all problems", "ru": "Показать все проблемы"},
    "ov_all_ok": {"en": "No active problems on any channel.", "ru": "Активных проблем нет ни на одном канале."},
    "ov_stream_problems": {"en": "Stream and transport problems now", "ru": "Проблемы потоков и транспорта сейчас"},
    "ov_top": {"en": "Most problematic channels", "ru": "Самые проблемные каналы"},
    "ov_channel": {"en": "Channel", "ru": "Канал"},
    "ov_share": {"en": "Compared", "ru": "Сравнение"},
    "ov_calculating": {"en": "Calculating…", "ru": "Расчёт…"},
    "ov_top_note": {
        "en": "Channels with problems in this period: {n}. Outage = {outage} alarms, degradation = {degraded}, merged like in the alarm report. Recalculated every 3 minutes.",
        "ru": "Каналов с проблемами за период: {n}. Недоступность — алармы {outage}, деградация — {degraded}, объединённые как в отчёте по алармам. Пересчёт раз в 3 минуты.",
    },
    "ov_quick_report": {"en": "Alarm report, last 24 h", "ru": "Отчёт по алармам за 24 ч"},
    "ov_open_wizard": {"en": "Open in the report wizard…", "ru": "Открыть в мастере отчётов…"},
    "ov_by_template": {"en": "Run a template for this channel", "ru": "Запустить шаблон для этого канала"},
    "ov_no_templates": {"en": "No templates yet", "ru": "Шаблонов пока нет"},
    "scope_label": {"en": "Server", "ru": "Сервер"},
    "scope_all": {"en": "All servers", "ru": "Все серверы"},
    "scope_hint": {
        "en": "Which connected server the overview and the report wizard show",
        "ru": "Какой из подключённых серверов показывают обзор и мастер отчётов",
    },
    "nav_servers": {"en": "Servers", "ru": "Серверы"},
    "servers_hint": {
        "en": "Nodes of every MultiProbe server you are connected to. Tick several to build one report across them.",
        "ru": "Узлы всех MultiProbe-серверов, к которым вы подключены. Отметьте несколько, чтобы построить общий отчёт.",
    },
    "err_not_connected": {
        "en": "You are not connected to the server “{name}”. Connect to it on the Servers page.",
        "ru": "Нет подключения к серверу «{name}». Подключитесь к нему на странице «Серверы».",
    },
    "err_server_unreachable": {"en": "The server is not reachable: {msg}", "ru": "Сервер недоступен: {msg}"},
    "err_not_api": {
        "en": "{url} answers, but not as the MultiProbe API. Enter the API address — usually the address of the "
              "MultiProbe web interface without a path (http://host:port) or port 40000.",
        "ru": "{url} отвечает, но это не API MultiProbe. Укажите адрес API — обычно это адрес веб-интерфейса "
              "MultiProbe без пути (http://хост:порт) или порт 40000.",
    },
    "err_server_unavailable": {"en": "Server “{name}” did not answer", "ru": "Сервер «{name}» не ответил"},
    "srv_add": {"en": "Add server", "ru": "Добавить сервер"},
    "srv_hint": {
        "en": "Independent MultiProbe installations. Every user sees this list and connects to each server with "
              "an account of that server; passwords are not stored, only the session.",
        "ru": "Независимые установки MultiProbe. Список общий для всех пользователей, а подключается каждый своей "
              "учётной записью этого сервера; пароли не хранятся — только сессия.",
    },
    "srv_name": {"en": "Name", "ru": "Название"},
    "srv_name_ph": {"en": "e.g. Site 2", "ru": "например, Площадка 2"},
    "srv_url": {"en": "API address", "ru": "Адрес API"},
    "srv_insecure": {
        "en": "Self-signed HTTPS certificate (do not verify)",
        "ru": "Самоподписанный HTTPS-сертификат (не проверять)",
    },
    "srv_insecure_tag": {"en": "certificate not verified", "ru": "сертификат не проверяется"},
    "srv_status": {"en": "Connection", "ru": "Подключение"},
    "srv_main": {"en": "main", "ru": "основной"},
    "srv_added_by": {"en": "Added by", "ru": "Добавил"},
    "srv_connected": {"en": "Connected", "ru": "Подключено"},
    "srv_not_connected": {"en": "Not connected", "ru": "Не подключено"},
    "srv_connect": {"en": "Connect", "ru": "Подключиться"},
    "srv_disconnect": {"en": "Disconnect", "ru": "Отключиться"},
    "srv_connect_to": {"en": "Connect to {name}", "ru": "Подключение к {name}"},
    "srv_connect_hint": {
        "en": "Use an account of that MultiProbe server. The password is used once to sign in and is not stored.",
        "ru": "Учётная запись этого сервера MultiProbe. Пароль используется один раз для входа и не сохраняется.",
    },
    "srv_connected_to": {"en": "Connected to {name}.", "ru": "Подключено к {name}."},
    "srv_saved": {"en": "Server “{name}” saved.", "ru": "Сервер «{name}» сохранён."},
    "srv_delete_confirm": {"en": "Remove server “{name}” from the list?", "ru": "Удалить сервер «{name}» из списка?"},
    "nav_templates": {"en": "Templates", "ru": "Шаблоны"},
    "template": {"en": "Template", "ru": "Шаблон"},
    "tpl_none": {"en": "— without a template —", "ru": "— без шаблона —"},
    "tpl_mine": {"en": "My templates", "ru": "Мои шаблоны"},
    "tpl_shared": {"en": "Shared by others", "ru": "Общие от других пользователей"},
    "tpl_save": {"en": "Save", "ru": "Сохранить"},
    "tpl_save_as": {"en": "Save as new template…", "ru": "Сохранить как новый шаблон…"},
    "tpl_manage": {"en": "Manage templates", "ru": "Управление шаблонами"},
    "tpl_name": {"en": "Template name", "ru": "Название шаблона"},
    "tpl_shared_hint": {"en": "Shared with all users", "ru": "Доступен всем пользователям"},
    "tpl_saved": {"en": "Template “{name}” saved.", "ru": "Шаблон «{name}» сохранён."},
    "tpl_missing_objects": {
        "en": "{n} object(s) from this template no longer exist in MultiProbe and were skipped.",
        "ru": "Объектов из шаблона больше нет в MultiProbe: {n}. Они пропущены.",
    },
    "tpl_copy_name": {"en": "Name of the copy:", "ru": "Название копии:"},
    "copy_of": {"en": "{name} (copy)", "ru": "{name} (копия)"},
    "cancel": {"en": "Cancel", "ru": "Отмена"},
    "tpl_create": {"en": "New template", "ru": "Новый шаблон"},
    "tpl_page_hint": {
        "en": "Run a template to get a fresh report in one click. Copy a template to make a similar one, "
              "for example for another channel.",
        "ru": "Запустите шаблон, чтобы получить свежий отчёт в один клик. Скопируйте шаблон, чтобы сделать "
              "похожий — например, для другого канала.",
    },
    "tpl_owner": {"en": "Owner", "ru": "Владелец"},
    "tpl_updated": {"en": "Updated", "ru": "Изменён"},
    "tpl_me": {"en": "me", "ru": "я"},
    "tpl_shared_tag": {"en": "shared", "ru": "общий"},
    "tpl_run": {"en": "Run", "ru": "Запустить"},
    "tpl_edit": {"en": "Edit", "ru": "Изменить"},
    "tpl_open": {"en": "Open", "ru": "Открыть"},
    "tpl_copy": {"en": "Copy", "ru": "Копировать"},
    "tpl_rename": {"en": "Rename", "ru": "Переименовать"},
    "tpl_share": {"en": "Share", "ru": "Сделать общим"},
    "tpl_unshare": {"en": "Make private", "ru": "Сделать личным"},
    "tpl_delete": {"en": "Delete", "ru": "Удалить"},
    "tpl_delete_confirm": {"en": "Delete template “{name}”?", "ru": "Удалить шаблон «{name}»?"},
    "tpl_empty": {
        "en": "No templates yet. Set up a report in the wizard and choose “Save as new template”.",
        "ru": "Шаблонов пока нет. Настройте отчёт в мастере и нажмите «Сохранить как новый шаблон».",
    },
    "last_n_hours": {"en": "Last {n} h", "ru": "Последние {n} ч"},
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
    "type_scte": {"en": "SCTE-35 markers", "ru": "SCTE-35 метки"},
    "type_scte_hint": {"en": "Ad insertion markers and breaks", "ru": "Метки и рекламные паузы"},
    "step_scte": {"en": "SCTE-35 sources", "ru": "Источники SCTE-35"},
    "scte_found": {"en": "SCTE-35 decoders in the selection: {n}", "ru": "SCTE-35 декодеров в выбранных объектах: {n}"},
    "scte_none_selected": {
        "en": "The selected objects have no SCTE-35 decoder. Select a stream whose preset records SCTE-35 to the database.",
        "ru": "У выбранных объектов нет SCTE-35 декодера. Выберите поток, пресет которого записывает SCTE-35 в базу.",
    },
    "scte_include_null": {"en": "Include splice_null (heartbeat) messages", "ru": "Включать сообщения splice_null (heartbeat)"},
    "sec_scte_summary": {"en": "Summary", "ru": "Сводка"},
    "sec_scte_timeline": {"en": "Ad break timeline", "ru": "Таймлайн рекламных пауз"},
    "sec_scte_missing": {"en": "Missing markers", "ru": "Пропущенные метки"},
    "sec_scte_list": {"en": "Marker list", "ru": "Список меток"},
    "report_scte_title": {"en": "SCTE-35 markers", "ru": "SCTE-35 метки"},
    "scte_no_objects": {
        "en": "None of the selected objects has an SCTE-35 decoder.",
        "ru": "Ни у одного из выбранных объектов нет SCTE-35 декодера.",
    },
    "scte_markers": {"en": "Markers", "ru": "Меток"},
    "scte_breaks": {"en": "Ad breaks", "ru": "Рекламных пауз"},
    "scte_break_total": {"en": "Total break time", "ru": "Суммарно пауз"},
    "scte_break_avg": {"en": "Average break", "ru": "Средняя пауза"},
    "scte_interval_avg": {"en": "Average interval", "ru": "Средний интервал"},
    "scte_missing": {"en": "Missing markers", "ru": "Пропущено меток"},
    "scte_missed": {"en": "Missed", "ru": "Пропущено"},
    "scte_last": {"en": "Last marker", "ru": "Последняя метка"},
    "scte_by_object": {"en": "By profile", "ru": "По профилям"},
    "scte_timeline": {"en": "Ad break timeline", "ru": "Таймлайн рекламных пауз"},
    "scte_break": {"en": "Ad break", "ru": "Рекламная пауза"},
    "scte_missing_title": {"en": "Missing markers", "ru": "Пропущенные метки"},
    "scte_received": {"en": "Received on", "ru": "Получена на"},
    "scte_missing_on": {"en": "Missing on", "ru": "Нет на"},
    "scte_no_missing": {
        "en": "Every marker reached all profiles of its stream.",
        "ru": "Каждая метка дошла до всех профилей своего потока.",
    },
    "scte_missing_note": {
        "en": "A marker counts as missing when other profiles of the same stream received it (same event ID) and this one did not.",
        "ru": "Метка считается пропущенной, если другие профили того же потока её получили (тот же event ID), а этот — нет.",
    },
    "scte_list": {"en": "Markers", "ru": "Метки"},
    "scte_all_commands": {"en": "All commands", "ru": "Все команды"},
    "scte_out": {"en": "OUT (to ad)", "ru": "OUT (на рекламу)"},
    "scte_in": {"en": "IN (back)", "ru": "IN (возврат)"},
    "col_command": {"en": "Command", "ru": "Команда"},
    "col_event_id": {"en": "Event ID", "ru": "Event ID"},
    "col_direction": {"en": "Direction", "ru": "Направление"},
    "col_break_sec": {"en": "Break, s", "ru": "Пауза, с"},
    "col_auto_return": {"en": "Auto return", "ru": "Автовозврат"},
    "col_cancel": {"en": "Cancel", "ru": "Отмена"},
    "col_segmentation": {"en": "Segmentation", "ru": "Сегментация"},
    "pdf_markers_limited": {
        "en": "The PDF lists the {n} most recent of {total} markers. The full list is in the CSV and XML export.",
        "ru": "В PDF показаны {n} последних из {total} меток. Полный список — в выгрузке CSV и XML.",
    },
    "step_content": {"en": "Report content", "ru": "Содержание отчёта"},
    "sections_hint": {
        "en": "Unticked sections are left out of the page and the PDF. CSV and XML always contain all data.",
        "ru": "Снятые разделы не попадут на страницу и в PDF. CSV и XML всегда содержат все данные.",
    },
    "pick_sections": {"en": "Select at least one report section.", "ru": "Выберите хотя бы один раздел отчёта."},
    "sec_alarms_summary": {"en": "Summary", "ru": "Сводка"},
    "sec_alarms_timeline": {"en": "Problem timeline", "ru": "Таймлайн проблем"},
    "sec_alarms_availability": {"en": "Availability by object", "ru": "Доступность по объектам"},
    "sec_alarms_top": {"en": "Most frequent alarms and objects", "ru": "Самые частые алармы и объекты"},
    "sec_alarms_list": {"en": "Alarm list", "ru": "Список алармов"},
    "sec_measures_charts": {"en": "Charts", "ru": "Графики"},
    "sec_measures_summary": {"en": "Summary", "ru": "Сводка"},
    "sec_measures_values": {"en": "Values table", "ru": "Таблица значений"},
    "pdf_page": {"en": "Page {n} of {total}", "ru": "Стр. {n} из {total}"},
    "pdf_alarm_list": {"en": "Alarms", "ru": "Алармы"},
    "pdf_rows_limited": {
        "en": "The PDF lists the {n} most recent of {total} alarms. The full list is in the CSV and XML export.",
        "ru": "В PDF показаны {n} последних из {total} алармов. Полный список — в выгрузке CSV и XML.",
    },
    "pdf_values_in_csv": {
        "en": "Values per time step are in the CSV and XML export.",
        "ru": "Значения по шагам времени — в выгрузке CSV и XML.",
    },
    "pick_measures": {"en": "Select at least one measurement.", "ru": "Выберите хотя бы одно измерение."},
    "err_raw_too_long": {
        "en": "Raw values are available for periods up to 24 hours. Choose a coarser resolution.",
        "ru": "Исходные значения доступны для периода до 24 часов. Выберите более крупный шаг.",
    },
    "resolution": {"en": "Resolution", "ru": "Детализация"},
    "resolution_hint": {
        "en": "Values are aggregated per time step (min / average / max). “Auto” keeps about 700 steps per object.",
        "ru": "Значения агрегируются по шагу времени (мин / среднее / макс). «Авто» — около 700 шагов на объект.",
    },
    "res_auto": {"en": "Auto", "ru": "Авто"},
    "res_1m": {"en": "1 minute", "ru": "1 минута"},
    "res_5m": {"en": "5 minutes", "ru": "5 минут"},
    "res_15m": {"en": "15 minutes", "ru": "15 минут"},
    "res_30m": {"en": "30 minutes", "ru": "30 минут"},
    "res_1h": {"en": "1 hour", "ru": "1 час"},
    "res_3h": {"en": "3 hours", "ru": "3 часа"},
    "res_6h": {"en": "6 hours", "ru": "6 часов"},
    "res_1d": {"en": "1 day", "ru": "1 день"},
    "res_raw": {"en": "Raw values (up to 24 h)", "ru": "Исходные значения (до 24 ч)"},
    "report_measures_title": {"en": "Measurements", "ru": "Измерения"},
    "values_loaded": {"en": "Values loaded", "ru": "Загружено значений"},
    "no_measure_data": {
        "en": "The selected objects have no values for these measurements in this period.",
        "ru": "У выбранных объектов нет значений этих измерений за период.",
    },
    "measure_summary": {"en": "Summary", "ru": "Сводка"},
    "measure_values": {"en": "Values", "ru": "Значения"},
    "all_measures": {"en": "All measurements", "ru": "Все измерения"},
    "measure_note": {
        "en": "Min and max are exact. Average and standard deviation are calculated by MultiProbe over all "
              "values of the period. Coverage is the share of time steps that have data.",
        "ru": "Минимум и максимум точные. Среднее и стандартное отклонение MultiProbe считает по всем значениям "
              "периода. Покрытие — доля шагов времени, в которых есть данные.",
    },
    "chart_limited": {"en": "Showing {n} of {total} objects — all are in the tables", "ru": "Показано {n} из {total} объектов — все есть в таблицах"},
    "chart_band_hint": {
        "en": "Line: average per time step. Shaded band: minimum to maximum.",
        "ru": "Линия — среднее за шаг времени. Заливка — от минимума до максимума.",
    },
    "col_time": {"en": "Time", "ru": "Время"},
    "col_analyzer": {"en": "Analyzer", "ru": "Анализатор"},
    "col_measure": {"en": "Measurement", "ru": "Измерение"},
    "col_unit": {"en": "Unit", "ru": "Ед. изм."},
    "col_value": {"en": "Value", "ru": "Значение"},
    "col_min": {"en": "Min", "ru": "Мин"},
    "col_avg": {"en": "Average", "ru": "Среднее"},
    "col_max": {"en": "Max", "ru": "Макс"},
    "col_std": {"en": "Std. dev.", "ru": "Ст. откл."},
    "col_coverage": {"en": "Coverage", "ru": "Покрытие"},
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
    "sum_affected": {"en": "Objects with outages", "ru": "Объектов с недоступностью"},
    "sum_availability": {"en": "Average availability", "ru": "Средняя доступность"},
    "sum_worst": {"en": "Most affected object", "ru": "Самый проблемный объект"},
    "timeline": {"en": "Problem timeline", "ru": "Таймлайн проблем"},
    "tl_outage": {"en": "Outage", "ru": "Недоступность"},
    "tl_outage_hint": {"en": "{outage}", "ru": "{outage}"},
    "tl_degraded": {"en": "Degraded", "ru": "Деградация"},
    "tl_degraded_hint": {"en": "{degraded}", "ru": "{degraded}"},
    "only_problems": {"en": "Only objects with problems", "ru": "Только объекты с проблемами"},
    "no_problems": {"en": "No problems in this period.", "ru": "За этот период проблем не было."},
    "availability_title": {"en": "Availability by object", "ru": "Доступность по объектам"},
    "col_availability": {"en": "Availability", "ru": "Доступность"},
    "col_incidents": {"en": "Incidents", "ru": "Инцидентов"},
    "col_longest": {"en": "Longest", "ru": "Самый долгий"},
    "col_kind": {"en": "Type", "ru": "Тип"},
    "availability_note": {
        "en": "Overlapping alarms count once. A service includes the alarms of its PIDs; streams and "
              "transports count only their own alarms. Times are clipped to the report period.",
        "ru": "Одновременные алармы считаются один раз. Сервис включает алармы своих PID; потоки и "
              "транспорт учитывают только собственные алармы. Время ограничено периодом отчёта.",
    },
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


# Which MultiProbe priority groups count as outage / degradation follows config.env, so the labels
# ("Outage (Error)") are filled in from the thresholds instead of being written into the strings.
_GROUPS = [(400, "Error"), (300, "Warning"), (200, "Caution"), (100, "Advisory"), (0, "Log")]
_THRESHOLDS = {
    "outage": ", ".join(n for lo, n in _GROUPS if lo >= settings.outage_min_priority) or "—",
    "degraded": ", ".join(n for lo, n in _GROUPS
                          if settings.degraded_min_priority <= lo < settings.outage_min_priority) or "—",
}
for _entry in STRINGS.values():
    for _lang, _text in _entry.items():
        if "{outage}" in _text or "{degraded}" in _text:
            _entry[_lang] = _text.replace("{outage}", _THRESHOLDS["outage"]).replace("{degraded}", _THRESHOLDS["degraded"])


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
