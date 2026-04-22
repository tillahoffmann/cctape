import sqlite3
from typing import Generator, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def iter_records(cursor: sqlite3.Cursor, record_type: type[T]) -> Generator[T]:
    columns = [column for column, *_ in cursor.description]
    for record in cursor:
        kwargs = dict(zip(columns, record))
        yield record_type(**kwargs)
