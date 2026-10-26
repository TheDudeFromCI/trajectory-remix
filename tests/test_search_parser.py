"""Query grammar and SQLite semantics without application/runtime dependencies."""

import sqlite3

import pytest

from trajectory_remix.search import SearchQuery, SearchSyntaxError, glob_match, register_search_functions


@pytest.mark.parametrize("query", [
    "(", ")", "()", "python)", "(python", "python |", "| python", "a || b",
    "-", "a -", '"unterminated', '""', "#", "@name:", "@name", "@unknown:x",
    "@state:", "@state:unknown", "@state:4", "@state:-4", "@state:*", "@batch:",
    "@windows:abc", "@windows:>5.2", "@windows:>-1", "@windows:=>2",
    "@windows:9223372036854775808", "x" * 4097,
    " ".join(["x"] * 257), "(" * 34 + "x" + ")" * 34, "-" * 34 + "x",
])
def test_invalid_queries_report_search_errors(query):
    with pytest.raises(SearchSyntaxError, match="Invalid search query"):
        SearchQuery(query)


@pytest.mark.parametrize("query,expected", [
    ("python -(math complex)", [0, 2]),
    ("human*", [0, 2]),
    ("a*e", [0, 2]),
    ("#coding @name:parallel-window-* @windows:1 @uses_tool:file_*", [0]),
    ("apple | banana -complex", [0, 2]),
    ("(apple | banana) -complex", [0]),
    ("-#*math*", [0, 1, 2]),
    ("-@name:*", [2]),
    ("--python", [0, 1, 2]),
    ("\t\n", [0, 1, 2]),
])
def test_query_predicates_execute_in_sqlite(query, expected):
    with sqlite3.connect(":memory:") as db:
        register_search_functions(db)
        db.executescript("""
            CREATE TABLE samples(uuid INTEGER, sample_id TEXT);
            CREATE TABLE trajectory_metadata(trajectory_uuid INTEGER, search TEXT, window_count INTEGER);
            CREATE TABLE metadata_tags(trajectory_uuid INTEGER, tag TEXT);
            CREATE TABLE trajectory_search_tools(trajectory_uuid INTEGER, tool TEXT);
            INSERT INTO samples VALUES(0,'parallel-window-one'),(1,'two'),(2,NULL);
            INSERT INTO trajectory_metadata VALUES
                (0,'python math human apple',1),
                (1,'python math complex superhuman banana',2),
                (2,'python complex humankind apple banana',3);
            INSERT INTO metadata_tags VALUES(0,'Coding');
            INSERT INTO trajectory_search_tools VALUES(0,'file_read');
        """)
        predicate, args = SearchQuery(query).sql()
        result = db.execute("SELECT s.uuid FROM samples s JOIN trajectory_metadata m ON m.trajectory_uuid=s.uuid WHERE " + predicate, args)
        assert [row[0] for row in result] == expected


@pytest.mark.parametrize("pattern,value,expected", [
    ("a?b", "axb", False), ("a?b", "a?b", True),
    ("[abc]", "a", False), ("[abc]", "[abc]", True),
    ("a.b", "axb", False), ("a.b", "a.b", True),
    ("a*b", "a\nb", True), ("strasse", "Straße", True),
    ("file_*", "file_read", True), ("file_*", None, False),
])
def test_globs_only_interpret_stars(pattern, value, expected):
    assert glob_match(pattern, value) is expected
