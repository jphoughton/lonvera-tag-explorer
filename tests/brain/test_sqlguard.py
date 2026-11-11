"""The SQL check: one SELECT or WITH statement, nothing that writes."""
import pytest

from brain.sqlguard import SqlRejected, check_select

OK = ["select 1", "SELECT 1;", "  with a as (select 1 as x) select * from a  ", "select case when 1 = 1 then 'a' else 'b' end",
      "select 'insert into x; drop table y' as s", 'select 1 as "delete"', "select 1 -- ; drop table x", "select /* update */ 1", "select $$a;b$$",
      "select day, sum(spend) from analytics.meta_ad_daily where day > current_date - 30 group by 1 order by 1 limit 5"]
BAD = [
    ("insert into app_meta (key, value) values ('a', 'b')", "only SELECT"), ("update products set name = 'x'", "only SELECT"), ("delete from ad_daily", "only SELECT"),
    ("drop table ad_daily", "only SELECT"), ("create table evil (a int)", "only SELECT"), ("alter table ad_daily add column x int", "only SELECT"), ("truncate ad_daily", "only SELECT"),
    ("grant all on ad_daily to public", "only SELECT"), ("copy ad_daily to stdout", "only SELECT"), ("copy (select 1) to program 'sh'", "only SELECT"),
    ("select 1; select 2", "one statement"), ("select 1; drop table x", "one statement"), ("select 1;; select 2", "one statement"),
    ("with d as (delete from ad_daily returning 1) select * from d", "delete"), ("with i as (insert into app_meta values ('a','b') returning 1) select * from i", "insert"),
    ("select * into newtab from ad_daily", "into"), ("select * from ad_daily for update", "update"), ("select pg_sleep(30)", "pg_sleep"), ("select set_config('a', 'b', false)", "set_config"),
    ("/* c */ delete from x", "only SELECT"), ("", "empty"), ("   ", "empty"), ("select 'unterminated", "unterminated"), ("select 1 /* open", "unterminated"),
    ("explain select 1", "only SELECT"), ("set role postgres", "only SELECT"), ("call p()", "only SELECT"), ("do $$ begin end $$", "only SELECT"), ("vacuum", "only SELECT"),
    ("select lo_import('/etc/passwd')", "lo_import"), ("select dblink('x', 'y')", "dblink"),
]


@pytest.mark.parametrize("q", OK)
def test_accepts_one_select(q):
    assert check_select(q)


@pytest.mark.parametrize("q,why", BAD)
def test_rejects_everything_else(q, why):
    with pytest.raises(SqlRejected, match=why):
        check_select(q)


def test_trailing_semicolon_is_dropped_and_the_text_is_kept():
    assert check_select("select 1 ; ") == "select 1"
    assert check_select("select ';'") == "select ';'"


def test_not_a_string_and_huge_queries_are_rejected():
    for bad in (None, 5, ["select 1"]):
        with pytest.raises(SqlRejected):
            check_select(bad)
    with pytest.raises(SqlRejected, match="longer"):
        check_select("select " + "1," * 20000 + "1")
