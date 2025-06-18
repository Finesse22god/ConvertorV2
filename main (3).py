from flask import Flask, request, jsonify, send_file
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
import json
import os
import threading
import time
import schedule
from collections import Counter
import re

app = Flask(__name__)


class AutoFeedConverter:

    def __init__(self):
        self.ns = {
            'realty': 'http://webmaster.yandex.ru/schemas/feed/realty/2010-06'
        }
        self.config_file = 'feed_config.json'
        self.jk_settings_file = 'jk_settings.json'
        self.output_file = 'avito_feed.xml'
        self.log_file = 'conversion_log.json'

        self.load_config()
        self.load_jk_settings()
        self.load_logs()

        # Запускаем планировщик в отдельном потоке
        self.start_scheduler()

    def load_config(self):
        """Загружаем основную конфигурацию"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            print(f"✅ Конфигурация загружена: {self.config}")
        except Exception as e:
            print(f"⚠️ Создаем новую конфигурацию: {e}")
            self.config = {
                'yandex_url': '',
                'auto_update': False,
                'update_time': '06:00',
                'last_update': None
            }

    def save_config(self):
        """Сохраняем конфигурацию"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            print(f"✅ Конфигурация сохранена")
        except Exception as e:
            print(f"❌ Ошибка сохранения конфигурации: {e}")

    def load_jk_settings(self):
        """Загружаем настройки ЖК"""
        try:
            if os.path.exists(self.jk_settings_file):
                with open(self.jk_settings_file, 'r', encoding='utf-8') as f:
                    self.jk_settings = json.load(f)
                print(
                    f"✅ Загружены настройки ЖК: {len(self.jk_settings)} элементов"
                )
            else:
                self.jk_settings = {}
                print("⚠️ Файл настроек ЖК не найден, создаем пустой")
        except Exception as e:
            print(f"❌ Ошибка загрузки настроек ЖК: {e}")
            self.jk_settings = {}

    def save_jk_settings(self):
        """Сохраняем настройки ЖК"""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(
                self.jk_settings_file)),
                        exist_ok=True)

            with open(self.jk_settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.jk_settings, f, ensure_ascii=False, indent=2)
            print(
                f"✅ Настройки ЖК сохранены: {len(self.jk_settings)} элементов")

            if os.path.exists(self.jk_settings_file):
                size = os.path.getsize(self.jk_settings_file)
                print(f"✅ Файл создан, размер: {size} байт")
            else:
                print("❌ Файл не создался!")

        except Exception as e:
            print(f"❌ Ошибка сохранения настроек ЖК: {e}")
            import traceback
            traceback.print_exc()

    def load_logs(self):
        """Загружаем логи конвертации"""
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                self.logs = json.load(f)
        except:
            self.logs = []

    def save_logs(self):
        """Сохраняем логи"""
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(self.logs, f, ensure_ascii=False, indent=2)

    def add_log(self, message, level='info'):
        """Добавляем запись в лог"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'message': message,
            'level': level
        }
        self.logs.append(log_entry)

        # Храним только последние 100 записей
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

        self.save_logs()
        print(f"[{level.upper()}] {message}")

    def get_jk_list(self):
        """Получаем список ЖК из текущего фида"""
        if not self.config['yandex_url']:
            return {}

        try:
            with urllib.request.urlopen(self.config['yandex_url']) as response:
                xml_content = response.read()

            root = ET.fromstring(xml_content)
            offers = root.findall('.//realty:offer', self.ns)

            jk_names = []
            for offer in offers:
                building_name = offer.find('.//realty:building-name', self.ns)
                if building_name is not None and building_name.text:
                    # Очищаем название от кавычек для упрощения
                    jk_name = building_name.text.strip().replace('"',
                                                                 '').replace(
                                                                     "'", "")
                    jk_names.append(jk_name)

            jk_counter = Counter(jk_names)
            return dict(jk_counter.most_common())

        except Exception as e:
            self.add_log(f"Ошибка загрузки фида: {e}", 'error')
            return {}

    def convert_feed(self, manual=False):
        """Основная функция конвертации"""
        if not self.config['yandex_url']:
            self.add_log("Не указана ссылка на фид Яндекса", 'error')
            return False

        source = "Ручная" if manual else "Автоматическая"
        self.add_log(f"{source} конвертация начата", 'info')

        try:
            # Загружаем фид
            with urllib.request.urlopen(self.config['yandex_url']) as response:
                xml_content = response.read()

            root = ET.fromstring(xml_content)
            offers = root.findall('.//realty:offer', self.ns)

            self.add_log(f"Найдено объявлений: {len(offers)}", 'info')

            # Статистика
            stats = {
                'total': len(offers),
                'with_custom': 0,
                'errors': 0,
                'jk_configured': len(self.jk_settings)
            }

            avito_ads = []

            for offer in offers:
                try:
                    ad_data = self.convert_offer(offer)
                    if ad_data:
                        # Применяем настройки ЖК
                        jk_name = self.get_jk_name(offer)

                        if jk_name:
                            self.add_log(
                                f"Обрабатываем объявление из ЖК: '{jk_name}'",
                                'info')

                            if jk_name in self.jk_settings:
                                self.add_log(
                                    f"✅ Найдены настройки для ЖК: '{jk_name}'",
                                    'info')
                                ad_data = self.apply_jk_settings(
                                    ad_data, jk_name)
                                stats['with_custom'] += 1
                            else:
                                self.add_log(
                                    f"❌ Настройки для ЖК '{jk_name}' не найдены",
                                    'warning')
                        else:
                            self.add_log("ЖК не определен для объявления",
                                         'warning')

                        avito_ads.append(ad_data)

                except Exception as e:
                    stats['errors'] += 1
                    self.add_log(f"Ошибка обработки объявления: {e}", 'error')

            # Генерируем XML
            xml_result = self.generate_avito_xml(avito_ads)

            # Сохраняем
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(xml_result)

            # Обновляем конфигурацию
            self.config['last_update'] = datetime.now().isoformat()
            self.save_config()

            # Логируем результат
            message = f"Конвертация завершена: {stats['total']} объявлений, {stats['with_custom']} с настройками, {stats['errors']} ошибок"
            self.add_log(message, 'success')

            return stats

        except Exception as e:
            self.add_log(f"Критическая ошибка конвертации: {e}", 'error')
            return False

    def get_jk_name(self, offer):
        """Получаем название ЖК"""
        # Пробуем разные варианты поиска названия ЖК
        building_name = offer.find('.//realty:building-name', self.ns)
        if building_name is not None and building_name.text:
            # Очищаем от кавычек для упрощения
            return building_name.text.strip().replace('"', '').replace("'", "")

        # Если нет building-name, пробуем другие поля
        development = offer.find('.//realty:new-development-name', self.ns)
        if development is not None and development.text:
            return development.text.strip().replace('"', '').replace("'", "")

        location = offer.find('.//realty:location', self.ns)
        if location is not None:
            district = location.find('.//realty:district', self.ns)
            if district is not None and district.text:
                return f"Район {district.text.strip()}"

        return None

    def apply_jk_settings(self, ad_data, jk_name):
        """Применяем настройки ЖК с правильной обработкой ID корпусов"""
        if jk_name not in self.jk_settings:
            return ad_data

        settings = self.jk_settings[jk_name]
        self.add_log(f"Применяем настройки для ЖК: {jk_name}", 'info')

        # Фотографии (максимум 40 по документации Авито)
        if settings.get('photos') and len(settings['photos']) > 0:
            valid_photos = [
                url.strip() for url in settings['photos']
                if url.strip().startswith(('http://', 'https://'))
            ]
            if valid_photos:
                ad_data['Images'] = valid_photos[:40]  # Лимит Авито
                self.add_log(
                    f"Добавлено {len(valid_photos)} фото для {jk_name}",
                    'info')

        # Описание (максимум 7500 символов по документации)
        if settings.get('description') and settings['description'].strip():
            try:
                description = settings['description'].format(
                    jk_name=jk_name,
                    rooms=ad_data.get('Rooms', ''),
                    square=ad_data.get('Square', ''),
                    floor=ad_data.get('Floor', ''),
                    floors=ad_data.get('Floors', ''),
                    price=ad_data.get('Price', ''))
                # Обрезаем до лимита Авито
                if len(description) > 7500:
                    description = description[:7497] + "..."
                ad_data['Description'] = description
                self.add_log(f"Обновлено описание для {jk_name}", 'info')
            except Exception as e:
                self.add_log(
                    f"Ошибка форматирования описания для {jk_name}: {e}",
                    'warning')

        # Изменение цены
        if settings.get('price_modifier') and 'Price' in ad_data:
            try:
                price = float(ad_data['Price'])
                modifier = settings['price_modifier'].strip()

                if modifier.endswith('%'):
                    percent = float(modifier[:-1])
                    new_price = price * (1 + percent / 100)
                    ad_data['Price'] = str(int(new_price))
                    self.add_log(
                        f"Изменена цена для {jk_name}: {price} -> {int(new_price)} ({modifier})",
                        'info')
                elif modifier and modifier != '0':
                    additional = float(modifier.replace('+', ''))
                    new_price = price + additional
                    ad_data['Price'] = str(int(new_price))
                    self.add_log(
                        f"Изменена цена для {jk_name}: {price} -> {int(new_price)} (+{additional}р)",
                        'info')
            except Exception as e:
                self.add_log(f"Ошибка изменения цены для {jk_name}: {e}",
                             'warning')

        # КРИТИЧЕСКИ ВАЖНО: Правильная обработка ID для новостроек
        if ad_data.get('MarketType') == 'Новостройка':

            # Логика выбора правильного ID:
            # 1. Если есть building_id (ID корпуса) - используем его в NewDevelopmentId
            # 2. Если building_id нет, но есть development_id (ID ЖК) - используем его

            building_id = settings.get('building_id', '').strip()
            development_id = settings.get('development_id', '').strip()

            if building_id and building_id.isdigit():
                # Приоритет у ID корпуса
                ad_data['NewDevelopmentId'] = building_id
                ad_data['PropertyRights'] = 'Застройщик'
                self.add_log(
                    f"✅ Использован ID корпуса как NewDevelopmentId для {jk_name}: {building_id}",
                    'info')

            elif development_id and development_id.isdigit():
                # Если нет ID корпуса, используем ID ЖК
                ad_data['NewDevelopmentId'] = development_id
                ad_data['PropertyRights'] = 'Застройщик'
                self.add_log(
                    f"✅ Использован ID ЖК как NewDevelopmentId для {jk_name}: {development_id}",
                    'info')

            else:
                # Если нет ни того, ни другого - убираем новостройку
                self.add_log(
                    f"❌ Нет валидных ID для новостройки {jk_name}, переводим во вторичку",
                    'warning')
                ad_data['MarketType'] = 'Вторичка'
                ad_data.pop('NewDevelopmentId', None)

            # Обязательные поля для новостроек
            if 'NewDevelopmentId' in ad_data:
                # Тип отделки
                if not ad_data.get('FinishType'):
                    ad_data['FinishType'] = 'Без отделки'

                # Статус объекта
                if not ad_data.get('Status'):
                    ad_data['Status'] = 'Квартира'

        return ad_data

    def convert_offer(self, offer):
        """Конвертируем одно объявление с правильной обработкой комнат"""
        offer_id = (offer.get('internal-id') or 
                   offer.get('id') or 
                   f"apt_{offer.get('internal-id', 'unknown')}")

        ad_data = {
            'Id': offer_id,
            'Category': 'Квартиры',
            'OperationType': 'Продам',
            'DateBegin': datetime.now().strftime('%Y-%m-%d'),
            'PropertyRights': 'Посредник'
        }

        # Основные поля
        field_mapping = {
            'ContactPhone': ('.//realty:phone', self.format_phone),
            'Description': ('.//realty:description', self.clean_description),
            'Price': ('.//realty:price/realty:value', str),
            'Square': ('.//realty:area/realty:value', str),
            'Floor': ('.//realty:floor', str),
            'Floors': ('.//realty:floors-total', str)
        }

        for field, (xpath, processor) in field_mapping.items():
            elem = offer.find(xpath, self.ns)
            if elem is not None and elem.text:
                try:
                    ad_data[field] = processor(elem.text)
                except:
                    pass

        # Заглушки для обязательных полей
        if 'ContactPhone' not in ad_data:
            ad_data['ContactPhone'] = '+79999999999'
        if 'Description' not in ad_data:
            ad_data['Description'] = 'Продается квартира'
        if 'Price' not in ad_data:
            ad_data['Price'] = '1000000'

        # Определяем тип рынка
        new_flat_elem = offer.find('.//realty:new-flat', self.ns)
        if new_flat_elem is not None and new_flat_elem.text == 'true':
            ad_data['MarketType'] = 'Новостройка'
            ad_data['PropertyRights'] = 'Застройщик'
        else:
            ad_data['MarketType'] = 'Вторичка'
            ad_data['PropertyRights'] = 'Посредник'

        ad_data['Status'] = 'Квартира'
        ad_data['HouseType'] = 'Монолитный'

        # ПРАВИЛЬНАЯ обработка комнат согласно Яндекс и Авито
        rooms_elem = offer.find('.//realty:rooms', self.ns)
        if rooms_elem is not None and rooms_elem.text:
            rooms_value = rooms_elem.text.strip().lower()

            # Яндекс использует "studio" для студий
            if rooms_value in ['studio', 'студия', '0']:
                ad_data['Rooms'] = 'Студия'  # Авито требует "Студия"
            elif rooms_value == '1':
                ad_data['Rooms'] = '1'
            elif rooms_value == '2':
                ad_data['Rooms'] = '2'
            elif rooms_value == '3':
                ad_data['Rooms'] = '3'
            elif rooms_value == '4':
                ad_data['Rooms'] = '4'
            elif rooms_value == '5':
                ad_data['Rooms'] = '5'
            elif rooms_value in ['6', '7', '8', '9']:
                ad_data['Rooms'] = rooms_value
            elif rooms_value.isdigit() and int(rooms_value) >= 10:
                ad_data['Rooms'] = '10 и более'
            else:
                # Пытаемся извлечь число
                try:
                    rooms_int = int(rooms_value)
                    if rooms_int == 0:
                        ad_data['Rooms'] = 'Студия'
                    elif 1 <= rooms_int <= 9:
                        ad_data['Rooms'] = str(rooms_int)
                    elif rooms_int >= 10:
                        ad_data['Rooms'] = '10 и более'
                    else:
                        ad_data['Rooms'] = '1'  # Значение по умолчанию
                except ValueError:
                    # Если не удалось распарсить - ставим по умолчанию
                    ad_data['Rooms'] = '1'

            self.add_log(f"Обработка комнат: '{rooms_elem.text}' -> '{ad_data['Rooms']}'", 'info')
        else:
            # Если поле rooms отсутствует
            ad_data['Rooms'] = '1'
            self.add_log("Поле rooms отсутствует, установлено значение '1'", 'warning')

        # Изображения
        images = []
        for image in offer.findall('.//realty:image', self.ns)[:40]:  # Лимит Авито
            if image.text and image.text.strip():
                img_url = image.text.strip()
                if img_url.startswith(('http://', 'https://')):
                    images.append(img_url)

        if images:
            ad_data['Images'] = images

        return ad_data

    def format_phone(self, phone):
        """Форматируем телефон"""
        if not phone:
            return '+79999999999'

        clean = re.sub(r'[^\d+]', '', phone)

        if clean.startswith('8'):
            return '+7' + clean[1:]
        elif clean.startswith('7'):
            return '+' + clean
        elif clean.startswith('+7'):
            return clean
        else:
            return '+7' + clean

    def clean_description(self, desc):
        """Очищаем описание"""
        if not desc:
            return 'Продается квартира'

        clean = re.sub(r'<[^>]+>', '', desc)
        clean = ' '.join(clean.split())
        return clean[:7500] if len(clean) > 7500 else clean

    def generate_avito_xml(self, ads_data):
        """Генерируем XML для Авито согласно официальной документации"""
        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>\n<Ads formatVersion="3" target="Avito.ru">'
        ]

        for ad_data in ads_data:
            xml_parts.append('  <Ad>')

            # Обязательные поля по документации
            mandatory_fields = {
                'Id': ad_data.get('Id', ''),
                'Category': 'Квартиры',  # Всегда "Квартиры" для квартир
                'OperationType': 'Продам',  # Всегда "Продам" 
                'ContactPhone': ad_data.get('ContactPhone', '+79999999999'),
                'Description': ad_data.get('Description',
                                           'Продается квартира'),
                'Price': ad_data.get('Price', '1000000'),
                'PropertyRights': ad_data.get('PropertyRights', 'Посредник')
            }

            # Добавляем обязательные поля
            for field, value in mandatory_fields.items():
                escaped_value = self.xml_escape(str(value))
                xml_parts.append(f'    <{field}>{escaped_value}</{field}>')

            # Дополнительные поля
            optional_fields = {
                'DateBegin': ad_data.get('DateBegin'),
                'Square': ad_data.get('Square'),
                'Floor': ad_data.get('Floor'),
                'Floors': ad_data.get('Floors'),
                'Rooms': ad_data.get('Rooms'),
                'MarketType': ad_data.get('MarketType', 'Вторичка'),
                'HouseType': ad_data.get('HouseType'),
                'Status': ad_data.get('Status', 'Квартира')
            }

            for field, value in optional_fields.items():
                if value:
                    escaped_value = self.xml_escape(str(value))
                    xml_parts.append(f'    <{field}>{escaped_value}</{field}>')

            # КРИТИЧЕСКИ ВАЖНО: NewDevelopmentId только для новостроек
            if (ad_data.get('MarketType') == 'Новостройка'
                    and ad_data.get('NewDevelopmentId')):

                dev_id = self.xml_escape(ad_data['NewDevelopmentId'])
                xml_parts.append(
                    f'    <NewDevelopmentId>{dev_id}</NewDevelopmentId>')

                # Добавляем тип отделки для новостроек
                if ad_data.get('FinishType'):
                    finish_type = self.xml_escape(ad_data['FinishType'])
                    xml_parts.append(
                        f'    <FinishType>{finish_type}</FinishType>')
                else:
                    xml_parts.append(
                        '    <FinishType>Без отделки</FinishType>')

            # Изображения (максимум 40 по документации)
            if 'Images' in ad_data and ad_data['Images']:
                images = ad_data['Images'][:40]  # Лимит Авито = 40 фото
                xml_parts.append('    <Images>')
                for img_url in images:
                    escaped_url = self.xml_escape(img_url)
                    xml_parts.append(f'      <Image url="{escaped_url}"/>')
                xml_parts.append('    </Images>')

            xml_parts.append('  </Ad>')

        xml_parts.append('</Ads>')
        return '\n'.join(xml_parts)

    def xml_escape(self, text):
        """Экранируем XML"""
        if not text:
            return ''
        return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace(
            '>', '&gt;').replace('"', '&quot;'))

    def scheduled_update(self):
        """Запланированное обновление"""
        self.add_log("Запуск автоматического обновления", 'info')
        self.convert_feed(manual=False)

    def start_scheduler(self):
        """Запускаем планировщик"""

        def run_scheduler():
            while True:
                try:
                    if self.config['auto_update']:
                        schedule.every().day.at(self.config['update_time']).do(
                            self.scheduled_update)
                    schedule.run_pending()
                    time.sleep(60)  # Проверяем каждую минуту
                except Exception as e:
                    print(f"Ошибка планировщика: {e}")
                    time.sleep(300)  # При ошибке ждем 5 минут

        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()


# Создаем глобальный экземпляр конвертера
converter = AutoFeedConverter()

# ====== WEB ROUTES ======


@app.route('/')
def index():
    """Главная страница с улучшенными подсказками"""
    return '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏠 Конвертер фидов Яндекс → Авито</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: #333;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }

        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }

        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }

        .tabs {
            display: flex;
            background: white;
            border-radius: 10px 10px 0 0;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }

        .tab {
            flex: 1;
            padding: 15px 20px;
            background: #f8f9fa;
            border: none;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            transition: all 0.3s ease;
            border-bottom: 3px solid transparent;
        }

        .tab:hover {
            background: #e9ecef;
        }

        .tab.active {
            background: white;
            border-bottom-color: #667eea;
            color: #667eea;
        }

        .content {
            background: white;
            border-radius: 0 0 10px 10px;
            padding: 30px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }

        .tab-content {
            display: none;
        }

        .tab-content.active {
            display: block;
        }

        .card {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            border-left: 4px solid #667eea;
        }

        .card h3 {
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.2em;
        }

        .form-group {
            margin-bottom: 15px;
        }

        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            color: #555;
        }

        .form-group input,
        .form-group textarea,
        .form-group select {
            width: 100%;
            padding: 10px;
            border: 2px solid #e9ecef;
            border-radius: 5px;
            font-size: 14px;
            transition: border-color 0.3s ease;
        }

        .form-group input:focus,
        .form-group textarea:focus,
        .form-group select:focus {
            outline: none;
            border-color: #667eea;
        }

        .form-group small {
            display: block;
            margin-top: 5px;
            font-size: 12px;
            color: #6c757d;
        }

        .btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s ease;
            margin-right: 10px;
            margin-bottom: 10px;
        }

        .btn:hover {
            background: #5a6fd8;
            transform: translateY(-2px);
        }

        .btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }

        .btn-success {
            background: #28a745;
        }

        .btn-success:hover {
            background: #218838;
        }

        .btn-danger {
            background: #dc3545;
        }

        .btn-danger:hover {
            background: #c82333;
        }

        .status {
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
            font-weight: 500;
        }

        .status.success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }

        .status.error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }

        .status.info {
            background: #d1ecf1;
            color: #0c5460;
            border: 1px solid #bee5eb;
        }

        .jk-list {
            max-height: 400px;
            overflow-y: auto;
        }

        .jk-item {
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 10px;
            transition: all 0.3s ease;
        }

        .jk-item:hover {
            border-color: #667eea;
            transform: translateY(-1px);
        }

        .jk-item.configured {
            border-color: #28a745;
            background: #f8fff9;
        }

        .logs {
            background: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 5px;
            padding: 15px;
            height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
        }

        .log-entry {
            margin-bottom: 5px;
            padding: 2px 0;
        }

        .log-info { color: #6c757d; }
        .log-success { color: #28a745; font-weight: bold; }
        .log-warning { color: #ffc107; }
        .log-error { color: #dc3545; font-weight: bold; }

        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }

        @media (max-width: 768px) {
            .grid {
                grid-template-columns: 1fr;
            }

            .tabs {
                flex-direction: column;
            }

            .container {
                padding: 10px;
            }
        }

        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
        }

        .modal-content {
            background-color: white;
            margin: 5% auto;
            padding: 30px;
            border-radius: 10px;
            width: 90%;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
        }

        .close {
            color: #aaa;
            float: right;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            line-height: 1;
        }

        .close:hover {
            color: #000;
        }

        .id-logic {
            background: #f0f8ff;
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
        }

        .id-logic h4 {
            color: #0066cc;
            margin-bottom: 10px;
        }

        .warning-text {
            color: #dc3545;
            font-weight: bold;
        }

        .success-text {
            color: #28a745;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏠 Конвертер фидов Яндекс → Авито</h1>
            <p>Автоматическая настройка объявлений по ЖК с фото и описаниями</p>
        </div>

        <div class="tabs">
            <button class="tab active" onclick="showTab('config')">⚙️ Настройки</button>
            <button class="tab" onclick="showTab('jk')">🏢 Настройка ЖК</button>
            <button class="tab" onclick="showTab('control')">🎮 Управление</button>
            <button class="tab" onclick="showTab('logs')">📋 Логи</button>
        </div>

        <div class="content">
            <!-- Вкладка настроек -->
            <div id="config" class="tab-content active">
                <div class="card">
                    <h3>🔗 Ссылка на фид Яндекса</h3>
                    <div class="form-group">
                        <label for="yandexUrl">URL фида Яндекс.Недвижимость:</label>
                        <input type="url" id="yandexUrl" placeholder="https://realty.yandex.ru/...">
                    </div>
                    <button class="btn" onclick="saveConfig()">Сохранить</button>
                    <button class="btn" onclick="loadJkList()">Обновить список ЖК</button>
                </div>

                <div class="card">
                    <h3>⏰ Автоматическое обновление</h3>
                    <div class="form-group">
                        <label>
                            <input type="checkbox" id="autoUpdate"> Включить автообновление
                        </label>
                    </div>
                    <div class="form-group">
                        <label for="updateTime">Время обновления:</label>
                        <input type="time" id="updateTime" value="06:00">
                    </div>
                    <button class="btn" onclick="saveConfig()">Сохранить настройки</button>
                </div>

                <div class="card">
                    <h3>📊 Статистика</h3>
                    <div id="stats">
                        <p>🏢 ЖК с настройками: <span id="configuredJkCount">0</span></p>
                        <p>📅 Последнее обновление: <span id="lastUpdate">Никогда</span></p>
                        <p>📄 Статус фида: <span id="feedStatus">Не создан</span></p>
                    </div>
                </div>
            </div>

            <!-- Вкладка настройки ЖК -->
            <div id="jk" class="tab-content">
                <div class="card">
                    <h3>🏢 Список ЖК из фида</h3>
                    <button class="btn" onclick="loadJkList()">Обновить список ЖК</button>
                    <button class="btn" onclick="debugSettings()">🔍 Проверить настройки</button>
                    <div id="jkList" class="jk-list">
                        <p>Загрузите фид для просмотра ЖК...</p>
                    </div>
                </div>
            </div>

            <!-- Вкладка управления -->
            <div id="control" class="tab-content">
                <div class="card">
                    <h3>🎮 Управление конвертацией</h3>
                    <button class="btn btn-success" onclick="manualConvert()">Конвертировать сейчас</button>
                    <button class="btn" onclick="downloadFeed()">Скачать фид</button>
                    <div id="conversionStatus"></div>
                </div>

                <div class="card">
                    <h3>🔗 Ссылки</h3>
                    <p><strong>Публичная ссылка на фид для Авито:</strong></p>
                    <p><code id="publicFeedUrl">Будет доступна после первой конвертации</code></p>
                    <button class="btn" onclick="copyFeedUrl()">Скопировать ссылку</button>
                </div>
            </div>

            <!-- Вкладка логов -->
            <div id="logs" class="tab-content">
                <div class="card">
                    <h3>📋 Логи работы</h3>
                    <button class="btn" onclick="loadLogs()">Обновить логи</button>
                    <div id="logsContainer" class="logs">
                        Загрузка логов...
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Модальное окно настройки ЖК -->
    <div id="jkModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal()">&times;</span>
            <h2>🏢 Настройка ЖК: <span id="modalJkName"></span></h2>

            <div class="form-group">
                <label for="jkPhotos">📸 Фотографии (по одной ссылке на строку, максимум 40):</label>
                <textarea id="jkPhotos" rows="5" placeholder="https://example.com/photo1.jpg\\nhttps://example.com/photo2.jpg"></textarea>
                <small>Авито принимает максимум 40 фотографий на объявление</small>
            </div>

            <div class="form-group">
                <label for="jkDescription">📝 Описание (максимум 7500 символов):</label>
                <textarea id="jkDescription" rows="5" placeholder="Продается квартира в ЖК {jk_name}. {rooms}-комнатная квартира площадью {square} кв.м. на {floor} этаже {floors}-этажного дома."></textarea>
                <small>Можно использовать переменные: {jk_name}, {rooms}, {square}, {floor}, {floors}, {price}</small>
            </div>

            <div class="form-group">
                <label for="jkPriceModifier">💰 Изменение цены:</label>
                <input type="text" id="jkPriceModifier" placeholder="+5% или +1000000">
                <small>Примеры: +5% (увеличить на 5%), +100000 (добавить 100тыс рублей)</small>
            </div>

            <div class="form-group">
                <label for="jkDevelopmentId">🏗️ ID ЖК в Авито:</label>
                <input type="text" id="jkDevelopmentId" placeholder="3166744">
                <small class="warning-text">⚠️ Должен существовать в справочнике Авито! Если ЖК нет - пишите на newdevelopments@avito.ru</small>
            </div>

            <div class="form-group">
                <label for="jkBuildingId">🏢 ID корпуса в Авито:</label>
                <input type="text" id="jkBuildingId" placeholder="222222">
                <small class="success-text">✅ Если указан - будет использован как основной ID новостройки (приоритет над ID ЖК)</small>
            </div>

            <div class="id-logic">
                <h4>💡 Логика выбора ID для NewDevelopmentId:</h4>
                <ol>
                    <li><strong>Если указан ID корпуса</strong> - используется он</li>
                    <li><strong>Если ID корпуса нет</strong> - используется ID ЖК</li>
                    <li><strong>Если нет ни того, ни другого</strong> - объект переводится во "Вторичку"</li>
                </ol>
                <p><small>Согласно документации Авито: если в ЖК есть корпуса, то в NewDevelopmentId должен быть ID корпуса, а не ID всего ЖК.</small></p>
            </div>

            <button class="btn btn-success" onclick="saveJkSettings()">Сохранить настройки ЖК</button>
        </div>
    </div>

    <script>
        let currentJkName = '';

        // Инициализация
        document.addEventListener('DOMContentLoaded', function() {
            loadConfig();
            loadLogs();
            updatePublicFeedUrl();
        });

        // Переключение вкладок
        function showTab(tabName) {
            // Скрываем все вкладки
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });

            // Показываем выбранную вкладку
            document.getElementById(tabName).classList.add('active');
            event.target.classList.add('active');

            // Загружаем данные для активной вкладки
            if (tabName === 'jk') {
                loadJkList();
            } else if (tabName === 'logs') {
                loadLogs();
            }
        }

        // Загрузка конфигурации
        async function loadConfig() {
            try {
                const response = await fetch('/api/config');
                const data = await response.json();

                document.getElementById('yandexUrl').value = data.config.yandex_url || '';
                document.getElementById('autoUpdate').checked = data.config.auto_update || false;
                document.getElementById('updateTime').value = data.config.update_time || '06:00';

                document.getElementById('configuredJkCount').textContent = data.jk_count;
                document.getElementById('lastUpdate').textContent = data.config.last_update ? 
                    new Date(data.config.last_update).toLocaleString('ru') : 'Никогда';
                document.getElementById('feedStatus').textContent = data.feed_exists ? 'Создан' : 'Не создан';

            } catch (error) {
                console.error('Ошибка загрузки конфигурации:', error);
            }
        }

        // Сохранение конфигурации
        async function saveConfig() {
            const config = {
                yandex_url: document.getElementById('yandexUrl').value,
                auto_update: document.getElementById('autoUpdate').checked,
                update_time: document.getElementById('updateTime').value
            };

            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });

                if (response.ok) {
                    showStatus('✅ Конфигурация сохранена!', 'success');
                    loadConfig();
                } else {
                    showStatus('❌ Ошибка сохранения конфигурации', 'error');
                }
            } catch (error) {
                showStatus('❌ Ошибка: ' + error.message, 'error');
            }
        }

        // Загрузка списка ЖК
        async function loadJkList() {
            try {
                const response = await fetch('/api/jk-list');
                const jkList = await response.json();

                const container = document.getElementById('jkList');

                if (jkList.length === 0) {
                    container.innerHTML = '<p>Сначала укажите ссылку на фид Яндекса в настройках</p>';
                    return;
                }

                let html = '';
                jkList.forEach(jk => {
                    html += `
                        <div class="jk-item ${jk.configured ? 'configured' : ''}">
                            <strong>${jk.name}</strong> (${jk.count} квартир)
                            <br>
                            Фото: ${jk.has_photos ? 'Есть' : 'Нет'} | 
                            Описание: ${jk.has_description ? 'Есть' : 'Нет'} |
                            ID ЖК: ${jk.has_development_id ? 'Есть' : 'Нет'} |
                            ID корпуса: ${jk.has_building_id ? 'Есть' : 'Нет'}
                            <br>
                            <button class="btn" onclick="editJk('${jk.name.replace(/'/g, '\\\\\\'')}')">
                                ${jk.configured ? 'Редактировать' : 'Настроить'}
                            </button>
                        </div>
                    `;
                });

                container.innerHTML = html;

            } catch (error) {
                document.getElementById('jkList').innerHTML = '<p>Ошибка загрузки списка ЖК: ' + error.message + '</p>';
            }
        }

        // Редактирование ЖК
        async function editJk(jkName) {
            currentJkName = jkName;
            document.getElementById('modalJkName').textContent = jkName;

            try {
                const response = await fetch('/api/jk-settings/' + encodeURIComponent(jkName));
                const settings = await response.json();

                document.getElementById('jkPhotos').value = settings.photos ? settings.photos.join('\\n') : '';
                document.getElementById('jkDescription').value = settings.description || '';
                document.getElementById('jkPriceModifier').value = settings.price_modifier || '';
                document.getElementById('jkDevelopmentId').value = settings.development_id || '';
                document.getElementById('jkBuildingId').value = settings.building_id || '';

                document.getElementById('jkModal').style.display = 'block';

            } catch (error) {
                showStatus('❌ Ошибка загрузки настроек ЖК: ' + error.message, 'error');
            }
        }

        // Сохранение настроек ЖК
        async function saveJkSettings() {
            const photos = document.getElementById('jkPhotos').value
                .split('\\n')
                .map(url => url.trim())
                .filter(url => url.startsWith('http'));

            const settings = {
                photos: photos,
                description: document.getElementById('jkDescription').value.trim(),
                price_modifier: document.getElementById('jkPriceModifier').value.trim(),
                development_id: document.getElementById('jkDevelopmentId').value.trim(),
                building_id: document.getElementById('jkBuildingId').value.trim()
            };

            try {
                const response = await fetch('/api/jk-settings/' + encodeURIComponent(currentJkName), {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify(settings)
                });

                const result = await response.json();

                if (response.ok && result.success) {
                    showStatus('✅ Настройки ЖК сохранены!', 'success');
                    closeModal();
                    loadJkList();
                    loadConfig();
                } else {
                    showStatus('❌ Ошибка сохранения настроек ЖК', 'error');
                }
            } catch (error) {
                showStatus('❌ Ошибка: ' + error.message, 'error');
            }
        }

        // Закрытие модального окна
        function closeModal() {
            document.getElementById('jkModal').style.display = 'none';
        }

        // Ручная конвертация
        async function manualConvert() {
            const button = event.target;
            const originalText = button.textContent;
            button.textContent = 'Конвертация...';
            button.disabled = true;

            try {
                const response = await fetch('/api/convert', { method: 'POST' });
                const result = await response.json();

                if (result.success) {
                    document.getElementById('conversionStatus').innerHTML = `
                        <div class="status success">
                            ✅ Конвертация завершена!<br>
                            Обработано: ${result.stats.total} объявлений<br>
                            С настройками: ${result.stats.with_custom}<br>
                            Ошибок: ${result.stats.errors}
                        </div>
                    `;
                    loadConfig();
                    loadLogs();
                    updatePublicFeedUrl();
                } else {
                    document.getElementById('conversionStatus').innerHTML = `
                        <div class="status error">❌ Ошибка конвертации: ${result.error}</div>
                    `;
                }
            } catch (error) {
                document.getElementById('conversionStatus').innerHTML = `
                    <div class="status error">❌ Ошибка: ${error.message}</div>
                `;
            } finally {
                button.textContent = originalText;
                button.disabled = false;
            }
        }

        // Скачивание фида
        async function downloadFeed() {
            try {
                const response = await fetch('/api/download-feed');
                if (response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'avito_feed.xml';
                    a.click();
                    window.URL.revokeObjectURL(url);
                } else {
                    showStatus('❌ Файл фида не найден. Сначала выполните конвертацию.', 'error');
                }
            } catch (error) {
                showStatus('❌ Ошибка скачивания: ' + error.message, 'error');
            }
        }

        // Отладка настроек
        async function debugSettings() {
            try {
                const response = await fetch('/api/debug-settings');
                const debug = await response.json();

                let message = 'ОТЛАДОЧНАЯ ИНФОРМАЦИЯ:\\n\\n';
                message += `Всего настроек ЖК: ${Object.keys(debug.jk_settings).length}\\n\\n`;

                for (const [jkName, settings] of Object.entries(debug.jk_settings)) {
                    message += `ЖК: ${jkName}\\n`;
                    message += `- Фото: ${settings.photos ? settings.photos.length : 0}\\n`;
                    message += `- Описание: ${settings.description ? 'Есть' : 'Нет'}\\n`;
                    message += `- Наценка: ${settings.price_modifier || 'Нет'}\\n`;
                    message += `- ID ЖК: ${settings.development_id || 'Нет'}\\n`;
                    message += `- ID корпуса: ${settings.building_id || 'Нет'}\\n\\n`;
                }

                alert(message);
            } catch (error) {
                showStatus('❌ Ошибка отладки: ' + error.message, 'error');
            }
        }

        // Загрузка логов
        async function loadLogs() {
            try {
                const response = await fetch('/api/logs');
                const logs = await response.json();

                const container = document.getElementById('logsContainer');

                if (logs.length === 0) {
                    container.innerHTML = 'Логи пусты';
                    return;
                }

                let html = '';
                logs.reverse().forEach(log => {
                    const date = new Date(log.timestamp).toLocaleString('ru');
                    const levelClass = `log-${log.level}`;
                    html += `<div class="log-entry ${levelClass}">[${date}] ${log.message}</div>`;
                });

                container.innerHTML = html;
                container.scrollTop = container.scrollHeight;

            } catch (error) {
                document.getElementById('logsContainer').innerHTML = 'Ошибка загрузки логов: ' + error.message;
            }
        }

        // Обновление публичной ссылки на фид
        function updatePublicFeedUrl() {
            const url = window.location.origin + '/feed.xml';
            document.getElementById('publicFeedUrl').textContent = url;
        }

        // Копирование ссылки на фид
        function copyFeedUrl() {
            const url = document.getElementById('publicFeedUrl').textContent;
            navigator.clipboard.writeText(url).then(() => {
                showStatus('✅ Ссылка скопирована в буфер обмена!', 'success');
            });
        }

        // Показ статуса
        function showStatus(message, type) {
            // Показываем в разных местах в зависимости от активной вкладки
            const activeTab = document.querySelector('.tab-content.active').id;
            let statusContainer;

            if (activeTab === 'config') {
                statusContainer = document.querySelector('#config .card:last-child');
            } else if (activeTab === 'control') {
                statusContainer = document.getElementById('conversionStatus');
            } else {
                // Создаем временный статус
                statusContainer = document.createElement('div');
                statusContainer.style.position = 'fixed';
                statusContainer.style.top = '20px';
                statusContainer.style.right = '20px';
                statusContainer.style.zIndex = '9999';
                document.body.appendChild(statusContainer);

                setTimeout(() => {
                    document.body.removeChild(statusContainer);
                }, 5000);
            }

            statusContainer.innerHTML = `<div class="status ${type}">${message}</div>`;
        }

        window.onclick = function(event) {
            const modal = document.getElementById('jkModal');
            if (event.target === modal) {
                closeModal();
            }
        }
    </script>
</body>
</html>'''


@app.route('/ping')
def ping():
    """Для поддержания активности в Replit"""
    return 'OK'


@app.route('/api/config', methods=['GET'])
def get_config():
    """Получить текущую конфигурацию"""
    return jsonify({
        'config': converter.config,
        'jk_count': len(converter.jk_settings),
        'feed_exists': os.path.exists(converter.output_file)
    })


@app.route('/api/config', methods=['POST'])
def save_config():
    """Сохранить конфигурацию"""
    data = request.json
    converter.config.update(data)
    converter.save_config()
    return jsonify({'success': True})


@app.route('/api/jk-list', methods=['GET'])
def get_jk_list():
    """Получить список ЖК"""
    jk_list = converter.get_jk_list()

    # Добавляем информацию о настройках
    result = []
    for jk_name, count in jk_list.items():
        settings = converter.jk_settings.get(jk_name, {})
        result.append({
            'name':
            jk_name,
            'count':
            count,
            'configured':
            bool(settings),
            'has_photos':
            bool(settings.get('photos')),
            'has_description':
            bool(settings.get('description')),
            'has_development_id':
            bool(settings.get('development_id')),
            'has_building_id':
            bool(settings.get('building_id'))
        })

    return jsonify(result)


@app.route('/api/jk-settings/<jk_name>', methods=['GET'])
def get_jk_settings(jk_name):
    """Получить настройки ЖК"""
    import urllib.parse
    jk_name_decoded = urllib.parse.unquote(jk_name)
    settings = converter.jk_settings.get(jk_name_decoded, {})
    print(f"🔍 Запрос настроек для ЖК: '{jk_name_decoded}'")
    return jsonify(settings)


@app.route('/api/jk-settings/<jk_name>', methods=['POST'])
def save_jk_settings(jk_name):
    """Сохранить настройки ЖК"""
    try:
        # Декодируем название ЖК из URL
        import urllib.parse
        jk_name_decoded = urllib.parse.unquote(jk_name)

        data = request.json
        print(f"🔧 Сохранение настроек для ЖК: '{jk_name_decoded}'")
        print(f"📝 Данные: {data}")

        if jk_name_decoded not in converter.jk_settings:
            converter.jk_settings[jk_name_decoded] = {}

        converter.jk_settings[jk_name_decoded].update(data)

        # Сохраняем в файл
        converter.save_jk_settings()

        # Проверяем что сохранилось
        converter.load_jk_settings()
        saved_settings = converter.jk_settings.get(jk_name_decoded, {})
        print(
            f"✅ Настройки сохранены для '{jk_name_decoded}': {saved_settings}")

        converter.add_log(f"Обновлены настройки ЖК: {jk_name_decoded}", 'info')

        return jsonify({'success': True, 'saved_settings': saved_settings})

    except Exception as e:
        print(f"❌ Ошибка сохранения настроек ЖК: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/convert', methods=['POST'])
def manual_convert():
    """Ручная конвертация"""
    result = converter.convert_feed(manual=True)
    if result:
        return jsonify({'success': True, 'stats': result})
    else:
        return jsonify({'success': False, 'error': 'Ошибка конвертации'})


@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Получить логи"""
    return jsonify(converter.logs[-30:])  # Последние 30 записей


@app.route('/api/download-feed', methods=['GET'])
def download_feed():
    """Скачать готовый фид"""
    if os.path.exists(converter.output_file):
        return send_file(converter.output_file,
                         as_attachment=True,
                         download_name='avito_feed.xml')
    else:
        return jsonify({'error': 'Файл не найден'}), 404


@app.route('/api/debug-settings', methods=['GET'])
def debug_settings():
    """Отладочная информация"""
    debug_info = {
        'jk_settings': converter.jk_settings,
        'config': converter.config,
        'settings_file_exists': os.path.exists(converter.jk_settings_file),
        'config_file_exists': os.path.exists(converter.config_file),
        'settings_file_path': os.path.abspath(converter.jk_settings_file),
        'current_dir': os.getcwd(),
        'files_in_dir': [f for f in os.listdir('.') if f.endswith('.json')]
    }
    return jsonify(debug_info)


@app.route('/feed.xml')
def public_feed():
    """Публичная ссылка на фид для Авито"""
    if os.path.exists(converter.output_file):
        return send_file(converter.output_file, mimetype='application/xml')
    else:
        return 'Feed not found', 404


if __name__ == '__main__':
    print("🚀 Запуск конвертера фидов Яндекс → Авито")
    print("📋 Функционал:")
    print("   ✅ Конвертация фидов")
    print("   ✅ Настройка ЖК (фото, описания, цены)")
    print("   ✅ Правильная обработка ID корпусов")
    print("   ✅ Автоматическое обновление")
    print("   ✅ Веб-интерфейс")
    print("   ✅ Логирование")
    print("   ✅ Отладка")
    print("")
    print("🌐 Веб-интерфейс будет доступен в окне справа")
    print("🏢 Логика ID: корпус (приоритет) → ЖК → вторичка")
    app.run(debug=False, host='0.0.0.0', port=5000)
