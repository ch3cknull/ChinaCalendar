"""Deterministic ICS generation. Annual holiday schedules are never inferred."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone
from lunar_python import Lunar, Solar

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = timedelta(days=1)
FEEDS = {
    "cal_holiday": "中华人民共和国法定节假日调休补班日历",
    "cal_holiday_1": "中华人民共和国法定节假日调休补班日历(时间段版本)",
    "cal_festival": "中华人民共和国节日纪念日日历",
    "cal_solarTerm": "中华人民共和国二十四节气日历",
    "cal_lunar": "中华人民共和国农历、天干地支日历",
    "cal_trunkBranch": "中华人民共和国天干地支、生辰八字日历",
    "countdown_holiday": "中华人民共和国法定节假日倒计时日历",
    "countdown_ChineseNewYear": "中华人民共和国春节倒计时日历",
    "calendar": "中华人民共和国节日、纪念日、法定节假日调休补班、二十四节气日历",
    "calendar_1": "中华人民共和国节日、纪念日、法定节假日调休补班、二十四节气日历(时间段版本)",
}


def read_json(path: Path):
    # Reject duplicate JSON keys instead of silently keeping the final value.
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError(f"{path}: 重复字段 {k}")
            obj[k] = v
        return obj
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def days(start: date, end: date):
    while start < end:
        yield start
        start += DAY


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("updated_at 必须包含时区")
    return stamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class Entry:
    key: str
    start: date | datetime
    end: date | datetime
    summary: str
    description: str
    category: str
    location: str = ""
    legacy_uid: str = ""


def load_schedules(data: Path, years: list[int]):
    schedules, off, working = [], set(), set()
    for year in years:
        path = data / "holidays" / f"{year}.json"
        if not path.exists():
            raise ValueError(f"缺少 {path}；不能推算年度放假调休安排")
        doc = read_json(path)
        if doc.get("schema_version") != 1 or doc.get("year") != year:
            raise ValueError(f"{path}: schema_version 或 year 错误")
        timestamp(doc["updated_at"])
        if not doc["holidays"]:
            raise ValueError(f"{path}: 年度假期不能为空")
        source = doc["source"]
        if source["status"] not in ("user-provided", "official") or not source["title"].strip():
            raise ValueError(f"{path}: 无效来源")
        if source["status"] == "official" and not (source.get("url") or "").startswith("https://"):
            raise ValueError(f"{path}: 官方来源必须包含 HTTPS 原文链接")
        ids = set()
        for h in doc["holidays"]:
            if not h["id"] or h["id"] in ids or not h["name"].strip():
                raise ValueError(f"{path}: 重复或空的节日标识")
            ids.add(h["id"])
            a, b = date.fromisoformat(h["start"]), date.fromisoformat(h["end"])
            if b < a or (b-a).days > 31 or not (a <= date(year, 12, 31) and b >= date(year, 1, 1)):
                raise ValueError(f"{path}: 无效假期时间段 {h['id']}")
            dates = set(days(a, b+DAY))
            ws = [date.fromisoformat(w) for w in h["workdays"]]
            if len(ws) != len(set(ws)) or any(w.year != year for w in ws):
                raise ValueError(f"{path}: 补班日期重复或年份错误")
            if off & dates or working & dates or set(ws) & (off | working | dates):
                raise ValueError(f"{path}: 假期或补班日期冲突 {h['id']}")
            off |= dates
            working.update(ws)
            schedules.append(dict(h, year=year, a=a, b=b, ws=ws, source=source, updated_at=doc["updated_at"]))
    return sorted(schedules, key=lambda h: h["a"])


def holiday_entries(schedules, periods):
    result = []
    ordinals = defaultdict(int)
    for h in schedules:
        ordinals[h['year']] += 1
        count = (h["b"]-h["a"]).days+1
        number = ordinals[h['year']]
        prefix = "一二三四五六七八九十"[number-1] if number <= 10 else str(number)
        desc = prefix + "、" + holiday_notice(h)
        for i, d in enumerate([h["a"]] if periods else days(h["a"], h["b"]+DAY), 1):
            suffix = f"共{count}天" if periods else f"第{i}天/共{count}天"
            result.append(Entry(f"{h['year']}-{h['id']}-holiday-{i}", d, h["b"]+DAY if periods else d+DAY,
                                f"「{h['name']} 假期」 {suffix}", desc, "假期",
                                legacy_uid=f"{d:%Y%m%d}-holiday" if periods else ""))
        for i, d in enumerate(h["ws"], 1):
            result.append(Entry(f"{h['year']}-{h['id']}-work-{i}", d, d+DAY,
                                f"「{h['name']} 补班」 第{i}天/共{len(h['ws'])}天", desc, "补班",
                                legacy_uid=f"{d:%Y%m%d}-workday" if periods else ""))
    return result


def chinese_date(d, *, omit_month=False, lunar=False):
    label = f"{d.day}日" if omit_month else f"{d.month}月{d.day}日"
    weekday = "周" + "一二三四五六日"[d.weekday()]
    extra = ""
    if lunar:
        l = Solar.fromYmd(d.year,d.month,d.day).getLunar()
        extra = "农历除夕" if l.next(1).getYear() != l.getYear() else f"农历{l.getMonthInChinese()}月{l.getDayInChinese()}"
    return label + "（" + (extra+"、" if extra else "") + weekday + "）"


def holiday_notice(h):
    count = (h["b"]-h["a"]).days+1
    spring = h["id"] == "spring-festival"
    span = chinese_date(h["a"],lunar=spring)
    if count > 1:
        span += "至" + chinese_date(h["b"],omit_month=h["a"].month==h["b"].month,lunar=spring)
    desc = f"{h['name']}：{span}放假{'调休' if h['ws'] else ''}，共{count}天。"
    if h["ws"]:
        desc += "、".join(chinese_date(w) for w in h["ws"]) + "上班。"
    desc += f"\n\n日期依据：{h['source']['title']}"
    desc += "\n放假通知："+h["source"]["url"] if h["source"].get("url") else "；未附可核验的国务院通知链接。"
    return desc


def solar_terms(year):
    # July's lunar-year table includes every term in the Gregorian year.
    table = Solar.fromYmd(year, 7, 1).getLunar().getJieQiTable()
    aliases = {"DONG_ZHI":"冬至", "DA_XUE":"大雪", "XIAO_HAN":"小寒", "DA_HAN":"大寒", "LI_CHUN":"立春", "YU_SHUI":"雨水", "JING_ZHE":"惊蛰"}
    found = {aliases.get(name,name): value for name, value in table.items() if value.getYear() == year}
    if len(found) != 24:
        raise ValueError(f"{year}: 应生成24个节气，实际{len(found)}")
    return found


def validate_rules(rules):
    allowed = {"solar", "lunar", "lunar_eve", "weekday", "term", "term_stem"}
    ids = set()
    for r in rules["rules"]:
        if not r["id"] or r["id"] in ids or not r["name"].strip() or r["kind"] not in allowed:
            raise ValueError(f"无效或重复的节日规则：{r}")
        ids.add(r["id"])
        limits = {}
        if r["kind"] in ("solar", "lunar", "weekday"):
            limits["month"] = (1,12)
        if r["kind"] in ("solar", "lunar"):
            limits["day"] = (1,30 if r["kind"] == "lunar" else 31)
        if r["kind"] in ("weekday", "term_stem"):
            limits["nth"] = (1,5)
        if r["kind"] == "weekday":
            limits["weekday"] = (0,6)
        for key,(low,high) in limits.items():
            if type(r[key]) is not int or not low <= r[key] <= high:
                raise ValueError(f"规则 {r['id']} 的 {key} 超出范围")
        if r["kind"] == "term_stem" and r["stem"] not in "甲乙丙丁戊己庚辛壬癸":
            raise ValueError(f"无效天干：{r['stem']}")


def resolve_rule(rule, year, terms):
    kind = rule["kind"]
    if kind == "solar":
        return date(year, rule["month"], rule["day"])
    if kind == "lunar":
        # Resolve both lunar years: Gregorian January can belong to the prior lunar year.
        for lunar_year in (year-1, year):
            try:
                s = Lunar.fromYmd(lunar_year, rule["month"], rule["day"]).getSolar()
            except ValueError:
                continue
            if s.getYear() == year:
                return date.fromisoformat(s.toYmd())
        return None
    if kind == "lunar_eve":
        return date.fromisoformat(Lunar.fromYmd(year, 1, 1).getSolar().toYmd()) - DAY
    if kind == "weekday":
        first = date(year, rule["month"], 1)
        d = first + timedelta(days=(rule["weekday"]-first.weekday()) % 7 + 7*(rule["nth"]-1))
        if d.month != first.month:
            raise ValueError(f"无效的第N个星期规则：{rule}")
        return d
    if kind == "term":
        return date.fromisoformat(terms[rule["term"]].toYmd()) + timedelta(days=rule.get("offset", 0))
    if kind == "term_stem":
        d = date.fromisoformat(terms[rule["term"]].toYmd()) + DAY
        count = 0
        for _ in range(70):
            if Solar.fromYmd(d.year,d.month,d.day).getLunar().getDayGan() == rule["stem"]:
                count += 1
                if count == rule["nth"]:
                    return d
            d += DAY
        raise ValueError(f"无效的节气天干规则：{rule}")
    raise ValueError(f"不支持的节日规则：{kind}")


def festival_entries(year, rules, terms, texts):
    result, ids = [], set()
    for r in rules["rules"]:
        if r["id"] in ids:
            raise ValueError(f"重复节日规则：{r['id']}")
        ids.add(r["id"])
        d = resolve_rule(r, year, terms)
        if d is None:
            continue
        title = r["name"].format(year=year, anniversary=year-r.get("since", year))
        description = r["description"].format(year=year, anniversary=year-r.get("since",year),
                                              description_anniversary=year-r.get("description_since",year))
        result.append(Entry(f"{year}-{r['id']}", d, d+DAY, f"『{title}』",
                            description + f"\n\n日期：{d:%Y年%m月%d日}", "节日纪念日"))
    if rules.get("seasonal_periods", True):
        seasonal = defaultdict(list)
        # Include full periods that start in this Gregorian year.
        for d in days(date(year, 1, 1), date(year+1, 4, 1)):
            lunar = Solar.fromYmd(d.year, d.month, d.day).getLunar()
            for season in (lunar.getFu(), lunar.getShuJiu()):
                if season:
                    start = d - timedelta(days=season.getIndex()-1)
                    if start.year == year:
                        seasonal[(start, season.getName())].append(d)
        for (a, name), dates in seasonal.items():
            b = max(dates)+DAY
            title = ("冬" if "九" in name else "") + name
            explanation = texts["shujiu"] if "九" in name else texts["seasonal"][name]
            result.append(Entry(f"{year}-season-{name}", a, b,
                                f"『{title}』{a.month}月{a.day}~{(b-DAY).month}月{(b-DAY).day}",
                                f"{title}，{a:%Y年%m月%d日}~{b-DAY:%Y年%m月%d日}。\n{explanation}", "时令"))
    return result


def countdown_entries(d, schedules, spring_only):
    candidates = [h for h in schedules if h["b"] >= d and (not spring_only or h["id"] == "spring-festival")]
    if candidates:
        h = candidates[0]
        n = (h["a"]-d).days
        name = f"{h['year']}年{h['name']}" if spring_only else h["name"]
        text = (f"明天{name}假期" if n == 1 else f"后天{name}假期" if n == 2 else f"还有{n}天{name}假期") if n > 0 else f"今天{name}假期 第{(d-h['a']).days+1}天/共{(h['b']-h['a']).days+1}天"
        desc = holiday_notice(h)
    elif spring_only:
        y = d.year
        target = date.fromisoformat(Lunar.fromYmd(y, 1, 1).getSolar().toYmd())
        if target < d:
            y += 1
            target = date.fromisoformat(Lunar.fromYmd(y, 1, 1).getSolar().toYmd())
        text = f"还有{(target-d).days}天{y}年春节（农历）"
        desc = "下一次春节假期安排尚未录入；此处仅计算农历正月初一，不代表放假起始日。"
    else:
        text, desc = "后续放假安排待录入", "已录入的假期全部结束；不推算下一年度的调休安排。"
    location = "" if spring_only else countdown_entries(d,schedules,True).summary.replace("『","「").replace("』","」")
    description = f"『{text}』" + ("\n"+location if location else "") + f"\n\n日期：{d:%Y年%m月%d日}\n" + desc
    return Entry(str(d), d, d+DAY, f"『{text}』", description, "倒计时",location)


def serialize(feed, entries, config):
    stamp = timestamp(config["updated_at"])
    cal = Calendar()
    for key, value in {"prodid": "-//ch3cknull//ChinaCalendar Generator//CN", "version": "2.0", "calscale": "GREGORIAN", "method": "PUBLISH", "x-wr-calname": FEEDS[feed], "x-wr-timezone": "Asia/Shanghai", "x-wr-caldesc": config["description"]}.items():
        cal.add(key, value)
    if any(isinstance(e.start, datetime) for e in entries):
        cal.add_component(Timezone.from_tzinfo(SHANGHAI, first_date=date(1970,1,1), last_date=date(2100,1,1)))
    seen = set()
    for entry in sorted(entries, key=lambda e: (e.start.isoformat(), e.key)):
        if entry.end <= entry.start:
            raise ValueError(f"事件结束时间无效：{entry.key}")
        if feed == "cal_holiday_1" and entry.legacy_uid:
            uid = entry.legacy_uid + "@" + config["uid_namespace"]
        else:
            uid = hashlib.sha256(f"{feed}:{entry.key}".encode()).hexdigest()[:32] + "@" + config["uid_namespace"]
        if uid in seen:
            raise ValueError(f"UID重复：{uid}")
        seen.add(uid)
        event = Event()
        description = entry.description.rstrip() + f"\n\n更新时间：{stamp:%Y-%m-%d}"
        for key, value in {"uid": uid, "dtstamp": stamp, "last-modified": stamp, "dtstart": entry.start, "dtend": entry.end, "summary": entry.summary, "description": description, "categories": entry.category, "status": "CONFIRMED", "transp": "TRANSPARENT", "sequence": config["revision"]}.items():
            event.add(key, value)
        if entry.location:
            event.add("location", entry.location)
        cal.add_component(event)
    return cal.to_ical()


def build(data: Path, years=None):
    config = read_json(data / "config.json")
    if config.get("schema_version") != 1:
        raise ValueError("无效配置版本")
    selected = years or config["years"]
    if selected == "auto":
        selected = [int(p.stem) for p in (data/"holidays").glob("[0-9][0-9][0-9][0-9].json")]
    years = sorted(set(selected))
    if not years or any(type(y) is not int or not 1901 <= y <= 2099 for y in years):
        raise ValueError("年份必须在1901至2099之间")
    schedules = load_schedules(data, years)
    rules = read_json(data / "festivals.json")
    texts = read_json(data / "event_text.json")
    if rules.get("schema_version") != 1:
        raise ValueError("无效节日规则版本")
    validate_rules(rules)
    if texts.get("schema_version") != 1:
        raise ValueError("无效事件说明版本")
    stamp = max(timestamp(value) for value in [config["updated_at"], rules["updated_at"],texts["updated_at"]] + [h["updated_at"] for h in schedules])
    config["updated_at"] = stamp.isoformat().replace("+00:00", "Z")
    # A monotonic sequence derived from source metadata; no second revision file to edit.
    config["revision"] = int((stamp-datetime(2000,1,1,tzinfo=timezone.utc)).total_seconds()//60)
    feeds = {name: [] for name in FEEDS}
    feeds["cal_holiday"] = holiday_entries(schedules, False)
    feeds["cal_holiday_1"] = holiday_entries(schedules, True)
    for year in years:
        terms = solar_terms(year)
        feeds["cal_festival"].extend(festival_entries(year, rules, terms,texts))
        for index,(name, s) in enumerate(sorted(terms.items(),key=lambda item:item[1].toYmd()),1):
            d = date.fromisoformat(s.toYmd())
            description = f"{name}，{year}年第{index}个节气\n{texts['solar_terms'][name]}\n\n交节时间：{s.toYmdHms()}（北京时间）"
            feeds["cal_solarTerm"].append(Entry(f"{year}-{name}", d, d+DAY, f"『{name}』",description, "节气"))
        for d in days(date(year,1,1), date(year+1,1,1)):
            l = Solar.fromYmd(d.year,d.month,d.day).getLunar()
            title = f"『{l.getMonthInChinese()}月 {l.getDayInChinese()} {l.getYear()}年』"
            location = f"{l.getYearInGanZhi()} {l.getYearShengXiao()}年 {l.getMonthInGanZhi()}月 {l.getDayInGanZhi()}日"
            description = f"{l.getYear()}年 {l.getMonthInChinese()}月 {l.getDayInChinese()}\n{location}\n日期：{d:%Y年%m月%d日}\n\n{texts['lunar_note']}"
            feeds["cal_lunar"].append(Entry(str(d), d, d+DAY, title,description, "农历",location))
            for hour in range(0,24,2):
                # Twelve consecutive two-hour periods: 子时 starts at 23:00 the previous day.
                a = datetime.combine(d, time(), SHANGHAI) + timedelta(hours=hour-1)
                b = a + timedelta(hours=2)
                eight = Solar.fromYmdHms(a.year,a.month,a.day,a.hour,0,0).getLunar().getEightChar()
                eight.setSect(1)  # Day changes at 23:00; year/month change at exact solar terms.
                y,m,day,t = eight.getYear(),eight.getMonth(),eight.getDay(),eight.getTime()
                animal = "鼠牛虎兔龙蛇马羊猴鸡狗猪"["子丑寅卯辰巳午未申酉戌亥".index(y[-1])]
                location = f"{y} {m} {day} {t}"
                description = f"{y}{animal}年 {m}月 {day}日 {t}时\n{location}\n\n时段：{a:%Y年%m月%d日 %H:%M}至{b:%Y年%m月%d日 %H:%M}（北京时间）\n{texts['ganzhi_note']}"
                feeds["cal_trunkBranch"].append(Entry(f"{d}-{hour}",a,b,f"『{t}时 {day}日 {m}月 {y}{animal}年』",description, "干支",location))
            for feed, spring in (("countdown_holiday",False),("countdown_ChineseNewYear",True)):
                feeds[feed].append(countdown_entries(d,schedules,spring))
    common = feeds["cal_festival"] + feeds["cal_solarTerm"]
    feeds["calendar"] = feeds["cal_holiday"] + common
    feeds["calendar_1"] = feeds["cal_holiday_1"] + common
    return {name+".ics":serialize(name,entries,config) for name,entries in feeds.items()}, feeds, config, schedules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,default=DATA)
    parser.add_argument("--year",type=int,action="append",help="可重复；默认使用config.json中的years")
    parser.add_argument("--output",type=Path,default=ROOT/"docs",help="输出目录；默认仅更新docs发布目录")
    parser.add_argument("--check",action="store_true",help="只检查生成结果是否与已提交文件一致")
    args = parser.parse_args()
    outputs, feeds, _, _ = build(args.data,args.year)
    changed = []
    for filename, content in outputs.items():
        p = args.output/filename
        if not p.exists() or p.read_bytes() != content:
            changed.append(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p))
            if not args.check:
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(content)
    if args.check and changed:
        parser.exit(1,"生成文件需要更新：\n" + "\n".join(changed) + "\n")
    for name, entries in feeds.items():
        print(f"{name}.ics: {len(entries)} events")
    print(f"{'检查通过' if args.check else '生成完成'}；{len(changed)}个文件{'需要更新' if args.check else '已更新'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        raise SystemExit(f"生成失败：{exc}") from exc
