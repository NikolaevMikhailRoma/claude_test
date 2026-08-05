"""Единая точка входа проекта. Планировщик дёргает main(), человек — отдельные стадии.

Пока каркас: тела наполняются по мере готовности модулей.
"""


def scraping():
    # обойти активные источники из config.json, сложить сырьё в data/raw/<источник>/
    # linkedin — единственный агентный шаг (Chrome); остальные источники — обычные скрипты
    pass


def parsing():
    # data/raw -> канонические записи -> ingest в mongo
    pass


def notify():
    # посты выше порога и без отметки в statuses -> telegram-бот -> отметка в statuses
    pass


def main():
    # scraping() -> parsing() -> notify()
    # позже между parsing и notify встанет скоринг (ml)
    pass


if __name__ == "__main__":
    main()
