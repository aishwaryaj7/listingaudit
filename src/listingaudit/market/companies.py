"""Fictional sellers, manufacturers and EU responsible persons.

Faker writes names and addresses in each country's local format. Emails and
websites use the reserved ".example" domain, so no generated address can belong
to a real company.

For China and Turkey, Faker has no English-language addresses, so we use small
templates instead.
"""

import re
import unicodedata
from dataclasses import dataclass

import numpy as np
from faker import Faker

# EU member states we generate companies in: ISO code -> (English name, Faker locale)
EU = {
    "DE": ("Germany", "de_DE"),
    "AT": ("Austria", "de_AT"),
    "PL": ("Poland", "pl_PL"),
    "IT": ("Italy", "it_IT"),
    "NL": ("Netherlands", "nl_NL"),
    "BE": ("Belgium", "nl_BE"),
    "FR": ("France", "fr_FR"),
    "ES": ("Spain", "es_ES"),
    "CZ": ("Czechia", "cs_CZ"),
    "PT": ("Portugal", "pt_PT"),
    "DK": ("Denmark", "da_DK"),
    "SE": ("Sweden", "sv_SE"),
    "FI": ("Finland", "fi_FI"),
    "HU": ("Hungary", "hu_HU"),
    "SK": ("Slovakia", "sk_SK"),
    "RO": ("Romania", "ro_RO"),
    "SI": ("Slovenia", "sl_SI"),
    "HR": ("Croatia", "hr_HR"),
}
# Outside the EU. The UK left in 2020 and Switzerland was never a member.
NON_EU = {
    "CN": ("China", None),
    "US": ("United States", "en_US"),
    "GB": ("United Kingdom", "en_GB"),
    "CH": ("Switzerland", "de_CH"),
    "TR": ("Turkey", None),
    "IN": ("India", "en_IN"),
}
COUNTRIES = EU | NON_EU
# Big, well-known EU countries, and smaller ones a checker may not recognise as EU
MAIN_EU = ["DE", "PL", "IT", "NL", "FR", "ES", "CZ", "AT", "BE"]
LESSER_KNOWN_EU = ["SI", "HR", "SK", "RO", "HU", "PT", "FI", "DK", "SE"]

LEGAL_FORMS = {
    "DE": "GmbH",
    "AT": "GmbH",
    "PL": "Sp. z o.o.",
    "IT": "S.r.l.",
    "NL": "B.V.",
    "BE": "BV",
    "FR": "SARL",
    "ES": "S.L.",
    "CZ": "s.r.o.",
    "PT": "Lda.",
    "DK": "ApS",
    "SE": "AB",
    "FI": "Oy",
    "HU": "Kft.",
    "SK": "s.r.o.",
    "RO": "S.R.L.",
    "SI": "d.o.o.",
    "HR": "d.o.o.",
    "US": "LLC",
    "GB": "Ltd",
    "CH": "AG",
    "IN": "Pvt. Ltd.",
}

CN_CITIES = [
    # city, province, postcode, districts
    (
        "Shenzhen",
        "Guangdong",
        "518000",
        ["Bao'an District", "Longgang District", "Nanshan District"],
    ),
    ("Guangzhou", "Guangdong", "510000", ["Baiyun District", "Panyu District", "Huadu District"]),
    ("Dongguan", "Guangdong", "523000", ["Chang'an Town", "Houjie Town", "Humen Town"]),
    ("Ningbo", "Zhejiang", "315000", ["Yinzhou District", "Beilun District", "Cixi"]),
    ("Yiwu", "Zhejiang", "322000", ["Futian Street", "Choujiang Street", "Beiyuan Street"]),
    (
        "Hangzhou",
        "Zhejiang",
        "310000",
        ["Xiaoshan District", "Yuhang District", "Binjiang District"],
    ),
    ("Xiamen", "Fujian", "361000", ["Huli District", "Jimei District", "Tong'an District"]),
    ("Suzhou", "Jiangsu", "215000", ["Wuzhong District", "Xiangcheng District", "Kunshan"]),
]
CN_NAMES = [
    "Huayuan", "Mingda", "Jiali", "Hengtai", "Xinyu", "Ruifeng", "Kaida", "Lianfa", "Haoyu",
    "Jinhui", "Yongsheng", "Tianrun", "Boyuan", "Chuangxin", "Longwei", "Shengda", "Weixing",
]  # fmt: skip
CN_ROADS = ["Xinhu", "Jianshe", "Renmin", "Zhongshan", "Huancheng", "Gongye", "Chuangye", "Fuhua"]
TR_CITIES = [("Istanbul", "34"), ("Bursa", "16"), ("Izmir", "35"), ("Denizli", "20")]
TR_NAMES = ["Akin", "Yildiz", "Ege", "Karaca", "Demir", "Atlas", "Dogan", "Aydin", "Sahin"]
TR_STREETS = ["Ataturk Cd.", "Cumhuriyet Cd.", "Istiklal Sk.", "Organize Sanayi Blv."]


@dataclass(frozen=True)
class Company:
    name: str
    country: str  # ISO code, e.g. "DE"
    street: str
    city: str  # postcode and city, in the local order
    email: str
    website: str

    @property
    def country_name(self) -> str:
        return COUNTRIES[self.country][0]

    @property
    def address(self) -> str:
        return f"{self.street}, {self.city}, {self.country_name}"

    @property
    def in_eu(self) -> bool:
        return self.country in EU


def slug(name: str) -> str:
    """'Müller Lighting' -> 'muller-lighting' (for fictional web addresses)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "-".join(re.findall(r"[a-z0-9]+", ascii_name.lower()))[:40].rstrip("-")


class CompanyFactory:
    """Makes fictional companies. The same seed always gives the same companies."""

    def __init__(self, seed: int, rng: np.random.Generator):
        locales = [loc for _, loc in COUNTRIES.values() if loc]
        self.fake = Faker(locales)
        self.fake.seed_instance(seed)
        self.rng = rng

    def _pick(self, options: list):
        return options[int(self.rng.integers(len(options)))]

    def company(self, country: str, trade: str, role: str = "maker") -> Company:
        """A company in `country`. `trade` is part of the name, e.g. 'Lighting'."""
        if country == "CN":
            return self._chinese(trade, role)
        if country == "TR":
            return self._turkish(trade, role)
        fake = self.fake[COUNTRIES[country][1]]
        short = f"{fake.last_name()} {trade}"
        street = fake.street_address().replace("\n", ", ")
        if country == "US":
            city = f"{fake.city()}, {fake.state_abbr()} {fake.zipcode()}"
        elif country in ("GB", "IN"):
            city = f"{fake.city()} {fake.postcode()}"
        else:
            city = f"{fake.postcode()} {fake.city()}"
        return self._finish(short, LEGAL_FORMS[country], country, street, city, role)

    def _chinese(self, trade: str, role: str) -> Company:
        city, province, postcode, districts = self._pick(CN_CITIES)
        short = f"{city} {self._pick(CN_NAMES)} {trade}"
        street = f"No. {int(self.rng.integers(1, 400))} {self._pick(CN_ROADS)} Road"
        place = f"{self._pick(districts)}, {city}, {province} {postcode}"
        return self._finish(short, "Co., Ltd.", "CN", street, place, role)

    def _turkish(self, trade: str, role: str) -> Company:
        city, plate = self._pick(TR_CITIES)
        short = f"{self._pick(TR_NAMES)} {trade}"
        street = f"{self._pick(TR_STREETS)} No: {int(self.rng.integers(1, 200))}"
        postcode = f"{plate}{int(self.rng.integers(100, 999))}"
        return self._finish(short, "San. ve Tic. A.S.", "TR", street, f"{postcode} {city}", role)

    def chinese_owned_eu(self, trade: str) -> Company:
        """A company with a Chinese name but registered in the EU: no EU person needed."""
        country = self._pick(["NL", "DE", "PL", "CZ", "HU"])
        fake = self.fake[EU[country][1]]
        city, *_ = self._pick(CN_CITIES)
        short = f"{city} {self._pick(CN_NAMES)} {trade} Europe"
        street = fake.street_address().replace("\n", ", ")
        place = f"{fake.postcode()} {fake.city()}"
        return self._finish(short, LEGAL_FORMS[country], country, street, place, "maker")

    def _finish(
        self, short: str, legal_form: str, country: str, street: str, city: str, role: str
    ) -> Company:
        """`short` is the name without its legal form; the web address is made from it."""
        domain = f"{slug(short)}.example"
        mailbox = {"maker": "info", "rp": "gpsr", "seller": "shop"}[role]
        name = f"{short} {legal_form}"
        return Company(name, country, street, city, f"{mailbox}@{domain}", f"https://www.{domain}")


class Directory:
    """Pools of companies, reused across listings like real suppliers are.

    Asking for a maker in the same trade and region a few times returns one of a
    handful of companies, so the same manufacturer appears on several listings.
    """

    POOL_SIZE = 4

    def __init__(self, factory: CompanyFactory, rng: np.random.Generator):
        self.factory = factory
        self.rng = rng
        self.pools: dict[tuple, list[Company]] = {}

    def get(self, trade: str, countries: list[str], role: str = "maker", size: int = 0) -> Company:
        key = (trade, tuple(countries), role)
        pool = self.pools.setdefault(key, [])
        if len(pool) < (size or self.POOL_SIZE):
            country = countries[int(self.rng.integers(len(countries)))]
            pool.append(self.factory.company(country, trade, role))
        return pool[int(self.rng.integers(len(pool)))]

    def chinese_owned_eu(self, trade: str) -> Company:
        pool = self.pools.setdefault(("chinese_owned_eu", trade), [])
        if len(pool) < 2:
            pool.append(self.factory.chinese_owned_eu(trade))
        return pool[int(self.rng.integers(len(pool)))]

    def eu_responsible_person(self) -> Company:
        return self.get("Product Compliance", MAIN_EU, "rp")

    def seller(self) -> Company:
        return self.get("Trading", MAIN_EU + ["CN"], "seller", size=25)
