"""Seed / refresh order-game clients and reviews from the curated spreadsheet."""
from django.core.management.base import BaseCommand

from games.models import OrderGameClient, OrderGameReview

CLIENTS = [
    {
        'company_name': 'Tinkoff',
        'logo_url': 'https://upload.wikimedia.org/wikipedia/commons/thumb/1/17/T-Bank_RU_logo.svg/1280px-T-Bank_RU_logo.svg.png',
        'sort_order': 10,
    },
    {
        'company_name': 'Glowbyte',
        'logo_url': 'https://static.tildacdn.com/tild3666-3462-4766-b765-643363363731/_.svg',
        'sort_order': 20,
    },
    {
        'company_name': 'Reversed.tech',
        'logo_url': 'https://reversed.tech/images/Rev.png',
        'sort_order': 30,
    },
]

REVIEWS = [
    {
        'name': 'Алексей Данилюк',
        'caption': 'чемпион мира по спортивному программированию 2020',
        'text': (
            'Десяточки - лучшая интеллектуальная игра на русском языке. '
            'Регулярно играем с друзьями, за последние 4 года не пропустил ни одной! '
            'Есть много интересных форматов заданий, как оригинальные (последовательности, замены, пропорции), '
            'так и отличные заимствованные (стены, ребусы, криптокроссворды); '
            'а иногда задания выходят за рамки форматов и получается что-то воистину уникальное! '
            'Я люблю решать замены и последовательности, а вот задания про слова предпочитаю оставлять сокомандникам :)'
        ),
        'is_important': True,
    },
    {
        'name': 'Артём Васильев',
        'caption': 'чемпион мира по спортивному программированию 2015',
        'text': (
            'С удовольствием играю в Десяточки с самого их зарождения, и они вдохновили меня на составление 20+ игр как автор.\n'
            'Главным преимуществом формата Десяточек является то, что открытый доступ к информации делает фокусом заданий '
            'не знание фактов, а умение замечать неожиданные закономерности и связывать разрозненные куски информации, '
            'часто от разных игроков в команде.\n'
            'У Андрея огромный опыт проведения игр, и он умеет составлять как сложные задания с глубоким погружением, '
            'так и быстрые простые задания; но и те, и те всегда крайне интересно решать.'
        ),
        'is_important': True,
    },
    {
        'name': 'Амаль Имангулов',
        'caption': 'чемпион России среди студентов по ЧГК',
        'text': (
            'Лучшие вещи делают из любви. «Десяточка» — это ода любви к программированию, дизайну и, конечно же, '
            'паззлам и загадкам в самом широком понимании этого слова. Абсолютно уникальный опыт командной работы, '
            'где для того, чтобы добиться успеха, необходимо разобраться в сильных сторонах участников команды '
            'и постоянно коммуницировать друг с другом. Отличный способ узнать своих коллег и друзей ещё лучше!'
        ),
        'is_important': True,
    },
    {
        'name': 'Рома',
        'caption': 'оператор ЭВМ',
        'text': (
            'Я уже несколько лет большой фанат Десяточек, особенно твоих текстовых паззлов (Триады и Замены — '
            'это лучший повод встать с утра и побежать на игру!), мы с друзьями не пропускаем ни одной игры! '
            'Ещё очень радуют крутые отсылки в тематике, много раз зачитывался чем-то новым после игр. '
            'Спасибо тебе за крутые игры!'
        ),
        'is_important': False,
    },
    {
        'name': 'Иван Кочкин',
        'caption': 'Инженер математических моделей',
        'text': (
            'Начал играть в десяточки почти 4 года назад и не пропустил ни одной с тех пор. '
            'Наиболее увлекательные формат паззлов/головоломок который мне известен на данный момент.'
        ),
        'is_important': False,
    },
    {
        'name': 'Соня',
        'caption': 'игрок команды «Икьсис Икьсисам»',
        'text': (
            'Играем в десяточку всей семьей! Круто, что каждый может найти задание по интересу, '
            'особенно интересно обсуждать и вместе находить решения всяких словесных загадок. '
            'А уж сколько всего нового осталось в голове благодаря Десяточкам — не счесть!'
        ),
        'is_important': False,
    },
    {
        'name': 'Анастасия',
        'caption': 'Ивент-менеджер Reversed.tech',
        'text': (
            'Огромное спасибо Андрею за «Десяточки», которые он делает для нашей компании!\n'
            'Каждый раз они становятся одной из самых ярких и запоминающихся частей мероприятия: вовлекают, '
            'создают азарт и превращают обычную активность в настоящее приключение. '
            'Для нас Андрей не просто автор игр, а настоящий партнёр, с которым можно придумывать, '
            'экспериментировать и создавать что-то по-настоящему особенное.'
        ),
        'is_important': True,
    },
]


class Command(BaseCommand):
    help = 'Load order-game clients (можно) and reviews (написал/написала).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be created/updated without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        client_count = 0
        review_count = 0

        for row in CLIENTS:
            if dry_run:
                self.stdout.write('client: {}'.format(row['company_name']))
                client_count += 1
                continue
            _, created = OrderGameClient.objects.update_or_create(
                company_name=row['company_name'],
                defaults={
                    'logo_url': row['logo_url'],
                    'sort_order': row['sort_order'],
                    'is_published': True,
                },
            )
            client_count += 1
            self.stdout.write(
                '{}{}'.format(
                    'created client ' if created else 'updated client ',
                    row['company_name'],
                )
            )

        for row in REVIEWS:
            if dry_run:
                self.stdout.write('review: {}'.format(row['name']))
                review_count += 1
                continue
            _, created = OrderGameReview.objects.update_or_create(
                name=row['name'],
                defaults={
                    'caption': row['caption'],
                    'text': row['text'],
                    'is_important': row['is_important'],
                    'is_published': True,
                },
            )
            review_count += 1
            self.stdout.write(
                '{}{}'.format(
                    'created review ' if created else 'updated review ',
                    row['name'],
                )
            )

        self.stdout.write(self.style.SUCCESS(
            'Done: {} clients, {} reviews{}'.format(
                client_count,
                review_count,
                ' (dry run)' if dry_run else '',
            )
        ))
