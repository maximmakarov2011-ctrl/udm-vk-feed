#!/usr/bin/env python3
"""
Конвертер YML-фида Яндекс Кита → YML для импорта товаров ВКонтакте.
Использование:
  python vk_feed.py [--out vk-feed.yml] [--images yastore|site]
"""
import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

SOURCE_URL = (
    "https://yastore-prod-persist.s3.yandex.net"
    "/feeds/yml/019f0969-b169-703b-9b2c-b9039ff1e69f.xml"
)

# Параметры для каждой категории (оставляем не более 2)
CATEGORY_PARAMS = {
    "17": ["Максимальная нагрузка", "Материал изделия"],  # Столики на кровать
    "11": ["Максимальная нагрузка", "Материал изделия"],  # Подносы для шашлыка
    "10": ["Количество отсеков", "Подходит для ящика"],   # Лотки для приборов
}
DEFAULT_PARAMS = ["Материал изделия", "Максимальная нагрузка"]

# Параметры и теги, которые всегда выкидываем
DROP_PARAMS = {"Цена WB", "Цена ОЗОН", "is_checkout_enabled"}
DROP_TAGS = {"collectionId", "custom_label_0", "typePrefix", "vendorCode", "barcode"}

# Параметр «Размер» с нулевым значением тоже убираем
DROP_PARAM_VALUES = {("Размер", "0")}


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def fetch_xml(url: str) -> ET.Element:
    with urllib.request.urlopen(url, timeout=60) as r:
        return ET.fromstring(r.read().decode("utf-8"))


def check_picture_content_type(url: str) -> bool:
    """HEAD-запрос: возвращает True если Content-Type начинается с image/."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as r:
            ct = r.headers.get("Content-Type", "")
            return ct.startswith("image/")
    except Exception:
        return False


def get_site_og_image(page_url: str) -> str | None:
    """Берёт og:image из HTML-страницы товара на udmgroup.ru."""
    try:
        req = urllib.request.Request(
            page_url, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(65536).decode("utf-8", errors="replace")
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def convert_offer(offer: ET.Element, images_mode: str) -> ET.Element:
    cat_id = offer.findtext("categoryId", "")
    keep_params = CATEGORY_PARAMS.get(cat_id, DEFAULT_PARAMS)

    new_offer = ET.Element("offer", id=offer.get("id"), available="true")

    # Копируем нужные простые теги
    for tag in ("name", "url", "price", "oldprice", "currencyId", "categoryId",
                "vendor", "description"):
        el = offer.find(tag)
        if el is None:
            continue
        if tag in DROP_TAGS:
            continue
        val = el.text or ""
        if tag == "description":
            val = strip_html(val)
        if tag == "oldprice":
            # Оставляем только если oldprice > price
            try:
                if float(val) <= float(offer.findtext("price", "0")):
                    continue
            except ValueError:
                continue
        new_el = ET.SubElement(new_offer, tag)
        new_el.text = val

    # Картинки (не более 5)
    pictures_src = [p.text for p in offer.findall("picture") if p.text][:5]

    if images_mode == "site":
        page_url = offer.findtext("url", "")
        og = get_site_og_image(page_url) if page_url else None
        if og:
            pictures_src = [og] + [p for p in pictures_src if p != og]

    # Оставляем первые 5, проверяем Content-Type только при необходимости
    for pic_url in pictures_src[:5]:
        pic_el = ET.SubElement(new_offer, "picture")
        pic_el.text = pic_url

    # Параметры: фильтруем, берём не более 2 из keep_params
    selected = []
    param_map = {p.get("name"): p.text for p in offer.findall("param")}
    for pname in keep_params:
        if pname in param_map:
            val = param_map[pname]
            if (pname, val) in DROP_PARAM_VALUES:
                continue
            if pname in DROP_PARAMS:
                continue
            selected.append((pname, val))
        if len(selected) == 2:
            break

    for pname, pval in selected:
        p_el = ET.SubElement(new_offer, "param", name=pname)
        p_el.text = pval

    return new_offer


def build_vk_feed(source_root: ET.Element, images_mode: str) -> ET.Element:
    src_shop = source_root.find("shop")

    root = ET.Element("yml_catalog", date="")
    shop = ET.SubElement(root, "shop")

    for tag in ("name", "company", "url"):
        el = src_shop.find(tag)
        if el is not None:
            new_el = ET.SubElement(shop, tag)
            new_el.text = el.text

    # currencies
    currencies = ET.SubElement(shop, "currencies")
    cur = ET.SubElement(currencies, "currency", id="RUB", rate="1")

    # categories
    categories = ET.SubElement(shop, "categories")
    for cat in src_shop.find("categories").findall("category"):
        c = ET.SubElement(categories, "category", id=cat.get("id"))
        if cat.get("parentId"):
            c.set("parentId", cat.get("parentId"))
        c.text = cat.text

    # offers
    offers_el = ET.SubElement(shop, "offers")
    src_offers = src_shop.find("offers").findall("offer")
    for offer in src_offers:
        offers_el.append(convert_offer(offer, images_mode))

    return root


def indent(elem: ET.Element, level: int = 0) -> None:
    pad = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = pad
        for child in elem:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = pad
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = pad


def validate(root: ET.Element) -> list[str]:
    errors = []
    offers = root.find("shop/offers")
    if offers is None:
        errors.append("Нет блока offers")
        return errors
    count = len(offers)
    if count == 0:
        errors.append("Офферов 0")
    for o in offers:
        oid = o.get("id", "?")
        if not o.findtext("name"):
            errors.append(f"offer {oid}: нет name")
        if not o.findtext("description"):
            errors.append(f"offer {oid}: нет description")
        if o.find("picture") is None:
            errors.append(f"offer {oid}: нет picture")
        price_txt = o.findtext("price", "")
        try:
            price = int(price_txt)
            if price <= 0:
                errors.append(f"offer {oid}: price={price} не > 0")
        except ValueError:
            errors.append(f"offer {oid}: price='{price_txt}' не целое")
        params = o.findall("param")
        if len(params) > 2:
            errors.append(f"offer {oid}: {len(params)} param > 2")
        for p in params:
            if "цена" in (p.get("name") or "").lower():
                errors.append(f"offer {oid}: param содержит 'цена': {p.get('name')}")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="vk-feed.yml")
    parser.add_argument("--images", choices=["yastore", "site"], default="yastore")
    args = parser.parse_args()

    print(f"Скачиваю исходный фид…")
    src = fetch_xml(SOURCE_URL)
    print("Конвертирую…")
    vk_root = build_vk_feed(src, args.images)

    indent(vk_root)
    tree = ET.ElementTree(vk_root)
    ET.register_namespace("", "")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="unicode", xml_declaration=False)
        f.write("\n")

    print(f"Записано: {args.out}")

    # Валидация
    errors = validate(vk_root)
    offers = vk_root.find("shop/offers")
    count = len(offers) if offers is not None else 0
    print(f"Офферов: {count}")
    if errors:
        print("ОШИБКИ ВАЛИДАЦИИ:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("Валидация: OK ✓")


if __name__ == "__main__":
    main()
