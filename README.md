# Парсер автомобильных объявлений Авито

Консольный прототип на Python. Из [Duff89/parser_avito](https://github.com/Duff89/parser_avito) взята схема «выдача → данные объявления → подробности → выгрузка» и ориентиры для разбора HTML. Прототип читает выдачу и карточки в браузере Playwright или разбирает локальные HTML-файлы.

## Установка в PowerShell

Из папки проекта. Если виртуальное окружение уже есть, пропустите первую строку.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Проверка на учебных данных

```powershell
.\.venv\Scripts\python.exe main.py --html examples/search.html --details --output-dir output/demo
```

Команда выгрузит два вымышленных объявления в `output/demo/cars.csv` и `output/demo/cars.sqlite3`. Chromium и доступ к Авито для этого запуска не нужны. Для собственного HTML сохраните выдачу в UTF-8. С `--details` скрипт ищет рядом файлы `<id>.html` для соответствующих объявлений.

## Запуск со ссылкой Авито

С установленным Microsoft Edge:

```powershell
.\.venv\Scripts\python.exe main.py --browser msedge --url "https://www.avito.ru/moskva/avtomobili" --pages 1 --limit 5
```

Для запуска с Chromium установите его через `.\.venv\Scripts\python.exe -m playwright install chromium` и уберите `--browser msedge`. Можно подставить ссылку на выдачу с нужными фильтрами. С флагом `--details` парсер посетит страницу каждого найденного объявления.

По умолчанию результат записывается в `output/cars.csv` и `output/cars.sqlite3`. SQLite хранит одну запись на ID, повторный запуск обновляет доступные поля. CSV содержит текущий снимок базы, использует UTF-8 с BOM и разделитель `;`. Базовые поля включают заголовок, цену в рублях, год, пробег, местоположение и ссылку. Описание сохраняется текстом, характеристики и ссылки фотографий хранятся в JSON-полях `characteristics` и `photo_urls`.

При ограничении доступа, CAPTCHA или нераспознанной выдаче команда сообщает причину, сохраняет уже собранные записи и возвращает ненулевой код. Результат онлайн-сбора зависит от разметки и доступности Авито.

Для диагностики онлайн-загрузки добавьте `--debug`. Скрипт выведет в stderr этап загрузки, HTTP-код, конечный URL без параметров запроса, заголовок страницы и значение `Retry-After`, если оно есть.
