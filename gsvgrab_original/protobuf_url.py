"""
Kodowanie protobufa w formacie URL używanym przez Mapy Google (parametr ``pb``).

Google nie publikuje tego formatu. Logika jest portem implementacji z projektu
``streetlevel`` (MIT, https://github.com/sk-zk/streetlevel) pliku
``streetlevel/streetview/protobuf.py``, która z kolei bazuje na obserwacji
ruchu klienta Map Google.

Format: ``!<tag><typ><wartość>`` gdzie typ to jedno z
``m`` (message), ``b`` (bool), ``d`` (double), ``e`` (enum), ``i`` (int), ``s`` (string).
Dla ``m`` liczba po literze to liczba pól zagnieżdżonej wiadomości.
"""


class ProtobufEnum:
    """Enum protobufa: w URL zapisywany jako liczba, ale musimy odróżnić go od int."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return "ProtobufEnum(%s)" % (self.value,)


def _datatype(value):
    if isinstance(value, str):
        return "s"
    if isinstance(value, bool):
        return "b"
    if isinstance(value, ProtobufEnum):
        return "e"
    if isinstance(value, int):
        return "i"
    if isinstance(value, float):
        return "d"
    if isinstance(value, dict):
        return "m"
    raise NotImplementedError("Nieobslugiwany typ protobufa: %r" % (value,))


def _field(tag, value):
    """Zwraca (liczba_podpol, tekst) dla pojedynczego pola."""
    if isinstance(value, list):
        serialized = ""
        count = 0
        for entry in value:
            sub_count, sub_serialized = _field(tag, entry)
            serialized += sub_serialized
            count += sub_count
        return count, serialized
    datatype = _datatype(value)
    if datatype == "m":
        return _message(tag, value)
    if datatype == "b":
        value = 1 if value else 0
    elif datatype == "e":
        value = value.value
    return 1, "!%s%s%s" % (tag, datatype, value)


def _message(tag, value):
    count = 0
    serialized = ""
    for field_tag, field_value in value.items():
        sub_count, sub_serialized = _field(field_tag, field_value)
        serialized += sub_serialized
        count += sub_count
    return count + 1, "!%sm%d%s" % (tag, count, serialized)


def to_protobuf_url(fields):
    """Zamienia słownik pól na ciąg ``pb`` używany przez Mapy Google."""
    serialized = ""
    for tag, value in fields.items():
        _, sub_serialized = _field(tag, value)
        serialized += sub_serialized
    return serialized
