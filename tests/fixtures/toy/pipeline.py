def fetch():
    return 1


def clean(x):
    return x


def save_it(x):
    print(x)


def process():
    raw = fetch()
    cleaned = clean(raw)
    save_it(cleaned)
