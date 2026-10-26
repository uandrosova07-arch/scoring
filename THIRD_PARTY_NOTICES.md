# Сторонние компоненты ML

Общая поставка: [NaOpite/alfacredit](https://github.com/NaOpite/alfacredit). Лицензии и notices сохраняются в пакетах и дистрибутивах; этот файл не заменяет их тексты.

## Python: сервис, тесты и обучение

| Пакет | Версия | Лицензия / метаданные PyPI | Использование |
|---|---|---|---|
| [fastapi](https://pypi.org/project/fastapi/0.115.6/) | 0.115.6 | MIT (классификатор PyPI) | сервис |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause | тесты |
| [numpy](https://pypi.org/project/numpy/2.5.3/) | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | генерация / обучение |
| [pandas](https://pypi.org/project/pandas/3.0.5/) | 3.0.5 | BSD-3-Clause (текст лицензии в метаданных пакета) | генерация / обучение |
| [pydantic](https://pypi.org/project/pydantic/2.10.4/) | 2.10.4 | MIT (классификатор PyPI) | сервис |
| [pytest](https://pypi.org/project/pytest/8.3.4/) | 8.3.4 | MIT | тесты |
| [scikit-learn](https://pypi.org/project/scikit-learn/1.9.1/) | 1.9.1 | BSD-3-Clause | генерация / обучение |
| [uvicorn](https://pypi.org/project/uvicorn/0.34.0/) | 0.34.0 | BSD-3-Clause | сервис |

`uvicorn[standard]` устанавливает дополнительные зависимости, указанные автором этого extra. Их licenses/notices сохраняются в установленных Python-пакетах. Исходные списки: `requirements.txt`, `requirements-dev.txt`, `requirements-train.txt` в ML-репозитории. Runtime загружает JSON и не требует NumPy, pandas или scikit-learn.

## Модель и данные

Используется **`logreg-synthetic-0.1` — логистическая регрессия команды**, обученная scikit-learn на синтетических данных, генерируемых `ml/generate.py`. Внешние банковские датасеты и сторонние предобученные веса не используются.

- В обучающем наборе 20 000 синтетических компаний и 29 886 заявок; seed `20261025`. Целевая переменная — синтетический `default_12m`.
- Источники: [описание модели](https://github.com/uandrosova07-arch/scoring/blob/main/docs/model.md), [генератор](https://github.com/uandrosova07-arch/scoring/blob/main/ml/generate.py), [обучение](https://github.com/uandrosova07-arch/scoring/blob/main/ml/train.py).
- JSON: `models/logreg-synthetic-0.1.json` в ML и `src/AlfaCredit.Core/Scoring/logreg-synthetic-0.1.json` в backend. SHA256 одинакового артефакта: `4783344256642d0da396a3312a134311f57e6a131ea72770cbde78c4baf18777`.
- Отпечаток наблюдаемых обучающих данных: `89fba5f6d12ac01c6eb37fb80f1a083c424969a91a6e7dede512220197dc3620`, формат `observed-columns-json-v1`.
- Качество на синтетике не подтверждает качество на реальных заёмщиках. PD — оценка модели на синтетике, а не вероятность одобрения заявки.
- `rules-0.3` — отдельный совместимый набор правил команды, а не сторонняя обученная модель.
- Демо-компании и реквизиты создаются seed backend; финансовые сведения и заявки синтетические. Скриншоты PRODUCT.md получены в этом прототипе. Качественные выводы опроса 10 предпринимателей предоставлены командой; персональные данные участников в поставку не включены.

## Runtime

Сервис работает в python:3.12-slim (PSF и сторонние notices Python, собственные лицензии пакетов ОС). Обучающий артефакт записывает Python 3.13.17. При запуске сервиса модель читается из JSON без scikit-learn, NumPy и pandas. Платные API, покупные датасеты и сторонние предобученные веса не используются. Кредитное решение принимает backend; ML возвращает оценку риска.
