from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import tempfile
import shutil
import subprocess
import sys
import unittest

from icalendar import Calendar
from .__main__ import ROOT, DATA, build, days, load_schedules, resolve_rule, serialize, solar_terms, validate_rules


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outputs, cls.feeds, cls.config, cls.schedules = build(DATA)
        cls.parsed = {name: Calendar.from_ical(content).walk("VEVENT") for name,content in cls.outputs.items()}

    def test_all_outputs_parse_and_match_counts(self):
        expected = {"cal_holiday":34,"cal_holiday_1":12,"cal_solarTerm":24,"cal_lunar":365,
                    "cal_trunkBranch":4380,"countdown_holiday":365,"countdown_ChineseNewYear":365}
        self.assertEqual(len(self.outputs),10)
        for name,count in expected.items():
            self.assertEqual(len(self.parsed[name+".ics"]),count,name)
        for name,events in self.parsed.items():
            uids = [str(e["UID"]) for e in events]
            self.assertEqual(len(uids),len(set(uids)),name)
            for e in events:
                self.assertLess(e.decoded("DTSTART"),e.decoded("DTEND"))

    def test_2027_periods_and_workdays(self):
        events = self.parsed["cal_holiday_1.ics"]
        periods = {(e.decoded("DTSTART"),e.decoded("DTEND")) for e in events if "假期" in str(e["SUMMARY"])}
        expected = {("2027-01-01","2027-01-04"),("2027-02-05","2027-02-14"),("2027-04-03","2027-04-06"),
                    ("2027-05-01","2027-05-06"),("2027-06-09","2027-06-10"),("2027-09-15","2027-09-16"),
                    ("2027-10-01","2027-10-08")}
        self.assertEqual(periods,{(date.fromisoformat(a),date.fromisoformat(b)) for a,b in expected})
        self.assertEqual(sum((b-a).days for a,b in periods),29)
        work = {e.decoded("DTSTART").isoformat() for e in events if "补班" in str(e["SUMMARY"])}
        self.assertEqual(work,{"2027-01-31","2027-02-14","2027-05-08","2027-09-26","2027-10-09"})
        self.assertTrue(all(type(e.decoded("DTSTART")) is date for e in events))
        self.assertEqual(str(next(e for e in events if e.decoded("DTSTART")==date(2027,2,5))["UID"]),
                         "20270205-holiday@ch3cknull-ChinaCalendar")

    def test_daily_and_period_calendars_cover_same_dates(self):
        for category in ("假期","补班"):
            def covered(feed):
                return {d for e in self.feeds[feed] if e.category==category for d in days(e.start,e.end)}
            self.assertEqual(covered("cal_holiday"),covered("cal_holiday_1"))

    def test_lunar_festivals_and_weekday_rules(self):
        events = {str(e["SUMMARY"]):e.decoded("DTSTART") for e in self.parsed["cal_festival.ics"]}
        self.assertEqual(events["『春节』"],date(2027,2,6))
        self.assertEqual(events["『除夕』"],date(2027,2,5))
        self.assertEqual(events["『端午节』"],date(2027,6,9))
        self.assertEqual(events["『中秋节』"],date(2027,9,15))
        self.assertEqual(events["『腊八节』"],date(2027,1,15))
        self.assertEqual(events["『母亲节』"],date(2027,5,9))
        # A regular lunar month does not duplicate its festival in the leap month.
        self.assertEqual(resolve_rule({"kind":"lunar","month":6,"day":1},2025,{}),date(2025,6,25))
        self.assertEqual(len(list(days(date(2028,1,1),date(2029,1,1)))),366)

    def test_solar_terms_are_complete_including_winter_solstice(self):
        terms = solar_terms(2027)
        self.assertEqual(len(terms),24)
        self.assertEqual(terms["清明"].toYmd(),"2027-04-05")
        self.assertEqual(terms["冬至"].toYmd(),"2027-12-22")

    def test_time_zone_and_ganzhi_period_boundaries(self):
        events = self.parsed["cal_trunkBranch.ics"]
        self.assertEqual(events[0].decoded("DTSTART").isoformat(),"2026-12-31T23:00:00+08:00")
        for i,e in enumerate(events):
            a,b = e.decoded("DTSTART"),e.decoded("DTEND")
            self.assertEqual(b-a,timedelta(hours=2))
            self.assertEqual(a.utcoffset(),timedelta(hours=8))
            if i:
                self.assertEqual(events[i-1].decoded("DTEND"),a)

    def test_no_invented_next_year_holiday(self):
        countdown = self.parsed["countdown_holiday.ics"][-1]
        self.assertEqual(str(countdown["SUMMARY"]),"『后续放假安排待录入』")
        spring = self.parsed["countdown_ChineseNewYear.ics"][-1]
        self.assertIn("2028年春节（农历）",str(spring["SUMMARY"]))
        self.assertIn("不代表放假起始日",str(spring["DESCRIPTION"]))

    def test_stable_bytes_uids_and_rfc_line_folding(self):
        for name,content in self.outputs.items():
            self.assertEqual(content,serialize(name[:-4],self.feeds[name[:-4]],self.config))
            self.assertTrue(content.endswith(b"\r\n"))
            self.assertNotIn(b"\n",content.replace(b"\r\n",b""))
            self.assertTrue(all(len(line)<=75 for line in content.split(b"\r\n")))
            for line in content.split(b"\r\n"):
                line.decode("utf-8")
        before = {str(e["UID"]) for e in self.parsed["cal_holiday_1.ics"]}
        changed = dict(self.config,revision=2,updated_at="2026-11-06T00:00:00Z")
        after = Calendar.from_ical(serialize("cal_holiday_1",self.feeds["cal_holiday_1"],changed)).walk("VEVENT")
        self.assertEqual(before,{str(e["UID"]) for e in after})

    def test_static_page_has_all_subscription_links(self):
        html = (ROOT/"docs/index.html").read_text()
        self.assertNotIn("{{",html)
        for name in self.outputs:
            self.assertIn(f'href="{name}"',html)

    def test_combined_feeds_composition(self):
        common = len(self.feeds["cal_festival"])+24
        self.assertEqual(len(self.feeds["calendar"]),34+common)
        self.assertEqual(len(self.feeds["calendar_1"]),12+common)

    def test_invalid_data_rejected(self):
        original = json.loads((DATA/"holidays/2027.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p/"holidays").mkdir()
            def check_bad(doc):
                (p/"holidays/2027.json").write_text(json.dumps(doc))
                with self.assertRaises(ValueError):
                    load_schedules(p,[2027])
            doc = deepcopy(original)
            doc["holidays"][0]["workdays"]=["2027-01-02"]
            check_bad(doc)
            doc = deepcopy(original)
            doc["holidays"][1]["start"]="2027-01-03"
            check_bad(doc)
            doc = deepcopy(original)
            doc["holidays"][0]["end"]="2026-12-31"
            check_bad(doc)
            doc = deepcopy(original)
            doc["holidays"][0]["workdays"]=["2027-01-31"]
            check_bad(doc)
            doc = deepcopy(original)
            doc["source"]["status"]="official"
            check_bad(doc)
            with self.assertRaises(ValueError):
                load_schedules(p,[2028])

    def test_one_annual_edit_updates_related_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/"data"
            shutil.copytree(DATA,target)
            p = target/"holidays/2027.json"
            doc = json.loads(p.read_text())
            doc["holidays"][1]["start"] = "2027-02-04"
            doc["updated_at"] = "2026-11-06T00:00:00Z"
            p.write_text(json.dumps(doc))
            outputs,feeds,config,schedules = build(target)
            self.assertEqual(len(feeds["cal_holiday"]),35)
            self.assertEqual(len(feeds["calendar"]),len(self.feeds["calendar"])+1)
            period = next(e for e in feeds["cal_holiday_1"] if "春节 假期" in e.summary)
            self.assertEqual(period.start,date(2027,2,4))
            self.assertIn("共10天",period.summary)
            countdown = next(e for e in feeds["countdown_holiday"] if e.start==date(2027,2,4))
            self.assertIn("今天春节假期",countdown.summary)
            self.assertGreater(config["revision"],self.config["revision"])

    def test_invalid_rule_cannot_silently_disappear(self):
        for rule in [
            {"id":"bad","name":"bad","kind":"lunar","month":13,"day":1},
            {"id":"bad","name":"bad","kind":"weekday","month":5,"weekday":7,"nth":2},
        ]:
            with self.assertRaises(ValueError):
                validate_rules({"rules":[rule]})

    def test_legacy_explanations_and_display_fields_are_restored(self):
        festival = {str(e["SUMMARY"]):e for e in self.parsed["cal_festival.ics"]}
        spring = str(festival["『春节』"]["DESCRIPTION"])
        self.assertIn("春节（农历正月初一）",spring)
        self.assertIn("集除旧布新、拜神祭祖、祈福辟邪、亲朋团圆",spring)
        self.assertIn("日期：2027年02月06日",spring)
        self.assertNotIn("data/festivals.json",spring)
        lunar = self.parsed["cal_lunar.ics"][0]
        self.assertIn("日期：2027年01月01日",str(lunar["DESCRIPTION"]))
        self.assertIn(str(lunar["LOCATION"]),str(lunar["DESCRIPTION"]))
        self.assertRegex(str(lunar["LOCATION"]),"[鼠牛虎兔龙蛇马羊猴鸡狗猪]年")
        ganzhi = self.parsed["cal_trunkBranch.ics"][0]
        self.assertRegex(str(ganzhi["SUMMARY"]),"[鼠牛虎兔龙蛇马羊猴鸡狗猪]年』$")
        self.assertIn(str(ganzhi["LOCATION"]),str(ganzhi["DESCRIPTION"]))
        self.assertIn("时段：",str(ganzhi["DESCRIPTION"]))
        countdown = self.parsed["countdown_holiday.ics"][0]
        self.assertIn("2027年春节假期",str(countdown["LOCATION"]))
        self.assertIn(str(countdown["LOCATION"]),str(countdown["DESCRIPTION"]))
        term = self.parsed["cal_solarTerm.ics"][0]
        self.assertIn("小寒，2027年第1个节气",str(term["DESCRIPTION"]))
        self.assertIn("交节时间：",str(term["DESCRIPTION"]))
        for events in self.parsed.values():
            for e in events:
                description = str(e["DESCRIPTION"])
                self.assertEqual(description.count("更新时间："),1)
                self.assertGreater(len(description),40)

    def test_migrated_anniversaries_update_without_rewriting_historical_years(self):
        festival = {str(e["SUMMARY"]):str(e["DESCRIPTION"]) for e in self.parsed["cal_festival.ics"]}
        hongkong = festival["『香港回归纪念日·30周年』"]
        self.assertIn("香港回归纪念日·30周年",hongkong)
        self.assertIn("1997年7月1日",hongkong)
        self.assertIn("王伟烈士牺牲26周年",festival["『81192，请返航。』"])
        self.assertIn("中华人民共和国成立78周年",festival["『中华人民共和国成立78周年』"])
        self.assertIn("中国共产党于1921年7月23日成立",festival["『中国共产党建党日·106周年』"])
        for text in festival.values():
            self.assertNotIn("{anniversary}",text)
            self.assertNotIn("更新时间：2026-08-19",text)

    def test_holiday_notice_contains_weekday_and_lunar_dates(self):
        spring = next(e for e in self.parsed["cal_holiday_1.ics"] if "春节 假期" in str(e["SUMMARY"]))
        desc = str(spring["DESCRIPTION"])
        self.assertIn("二、春节",desc)
        self.assertIn("2月5日（农历除夕、周五）",desc)
        self.assertIn("13日（农历正月初八、周六）",desc)
        self.assertIn("1月31日（周日）、2月14日（周日）上班",desc)

    def test_new_annual_file_is_auto_discovered_and_leap_day_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/"data"
            shutil.copytree(DATA,target)
            fixture = {"schema_version":1,"year":2028,"updated_at":"2027-11-01T00:00:00Z",
                       "source":{"status":"user-provided","title":"test fixture, not an actual schedule","url":None},
                       "holidays":[{"id":"new-year","name":"元旦","start":"2028-01-01","end":"2028-01-01","workdays":[]}]}
            (target/"holidays/2028.json").write_text(json.dumps(fixture))
            _,feeds,_,_ = build(target)
            self.assertEqual(len(feeds["cal_lunar"]),731)
            self.assertEqual(len(feeds["cal_solarTerm"]),48)
            self.assertTrue(any(e.start==date(2028,2,29) for e in feeds["cal_lunar"]))

    def test_calendar_generation_preserves_static_homepage(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/"site"
            output.mkdir()
            homepage = b"<!doctype html><title>Independent static homepage</title>"
            (output/"index.html").write_bytes(homepage)
            subprocess.run([sys.executable,"-m","generator","--output",str(output)],
                           cwd=ROOT,check=True,capture_output=True,text=True)
            expected = set(self.outputs) | {"index.html"}
            self.assertEqual({p.name for p in output.iterdir()},expected)
            self.assertEqual((output/"index.html").read_bytes(),homepage)
            self.assertEqual((output/"cal_holiday_1.ics").read_bytes(),self.outputs["cal_holiday_1.ics"])


if __name__ == "__main__":
    unittest.main()
