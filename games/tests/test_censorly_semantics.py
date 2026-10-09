"""Gameplay semantics: every possible reading, full root-set equality."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from games.censorly.lexical.core import fold
from games.censorly.lexical.semantics import explain, initially_open, opens

TRUE_CASES = (
    ('пчела', 'пчёлами', 'lexeme'),
    ('улей', 'улья', 'lexeme'),
    ('улей', 'ульями', 'lexeme'),
    ('белка', 'белки', 'lexeme'),
    ('белок', 'белки', 'lexeme'),
    ('Белка', 'белки', 'lexeme'),
    ('БЕЛКА', 'белки', 'lexeme'),
    ('прибыть', 'прибыли', 'lexeme'),
    ('прибыль', 'прибыли', 'lexeme'),
    ('ходить', 'выходить', 'root'),
    ('ходить', 'приходить', 'root'),
    ('писать', 'написать', 'root'),
    ('мыть', 'мыться', 'root'),
    ('открыть', 'открыться', 'root'),
    ('работа', 'работать', 'root'),
    ('работа', 'рабочий', 'root'),
    ('сила', 'сильный', 'root'),
    ('белый', 'белизна', 'root'),
    ('читать', 'чтение', 'root'),
    ('дом', 'домик', 'root'),
    ('кот', 'котик', 'root'),
    ('рука', 'ручка', 'root'),
    ('нога', 'ножка', 'root'),
    ('книга', 'книжка', 'root'),
    ('мать', 'матушка', 'root'),
    ('человек', 'люди', 'lexeme'),
    ('ребёнок', 'дети', 'lexeme'),
    ('год', 'лет', 'lexeme'),
    ('хороший', 'лучше', 'lexeme'),
    ('хороший', 'лучший', 'lexeme'),
    ('плохой', 'хуже', 'lexeme'),
    ('большой', 'больший', 'grammar'),
    ('большой', 'больше', 'lexeme'),
    ('я', 'меня', 'lexeme'),
    ('мы', 'нас', 'lexeme'),
    ('один', 'первый', 'grammar'),
    ('два', 'второй', 'grammar'),
    ('три', 'третий', 'root'),
    ('пять', 'пятый', 'root'),
    # «пара» is also the genitive of «пар» (steam). That reading is enough.
    ('пара', 'испарять', 'root'),
    ('строить', 'страивать', 'root'),
    # Short masculine of «сырой» is also «сыр». Damp stays linked.
    ('сырой', 'сырость', 'root'),
    ('пологий', 'пологость', 'root'),
    # «устав» is also the gerund of «устать», so the surface reaches the fatigue family.
    ('устав', 'устать', 'lexeme'),
    ('устав', 'уставать', 'root'),
    ('устать', 'усталость', 'root'),
    ('стоять', 'стоянка', 'root'),
    ('стать', 'становиться', 'root'),
    ('ставить', 'ставка', 'root'),
    ('образ', 'образование', 'root'),
    ('разить', 'поражать', 'root'),
    ('отражать', 'поражать', 'root'),
    ('отражатель', 'отражение', 'root'),
    ('запас', 'запасать', 'root'),
    ('спасти', 'спасение', 'root'),
    ('плести', 'плетение', 'root'),
    ('грести', 'гребля', 'root'),
    ('марь', 'маревый', 'root'),
    ('мир', 'мировой', 'root'),  # multi: peace|world
    ('мирный', 'перемирие', 'root'),
    ('мирской', 'мирянин', 'root'),
    ('смирный', 'смирить', 'root'),
    ('море', 'морской', 'root'),
    ('вести', 'водить', 'root'),
    ('завод', 'заводский', 'root'),
    ('общий', 'общность', 'root'),
    ('общество', 'общественный', 'root'),
    ('один', 'однажды', 'root'),
    ('средний', 'середина', 'root'),
    ('длина', 'длинный', 'root'),
    ('удар', 'ударить', 'root'),
    ('равный', 'равенство', 'root'),
    ('верх', 'вершина', 'root'),
    ('совершить', 'завершить', 'root'),
    ('гадкий', 'гадость', 'root'),
    ('следить', 'слежка', 'root'),
    ('следствие', 'расследование', 'root'),
    ('умирать', 'смерть', 'root'),
    ('умереть', 'мертвый', 'root'),
    ('морить', 'моровой', 'root'),
    ('замирать', 'замереть', 'root'),
    ('направление', 'направить', 'root'),
    ('правительство', 'правитель', 'root'),
    ('справка', 'справочник', 'root'),
    ('правда', 'правдивый', 'root'),
    ('править', 'правительство', 'root'),
    ('править', 'исправить', 'root'),
    ('заправка', 'заправить', 'root'),
    ('приправа', 'заправить', 'root'),
    ('совет', 'советовать', 'root'),
    ('ответ', 'ответить', 'root'),
    ('ответственность', 'ответственный', 'root'),
    ('привет', 'приветствие', 'root'),
    ('соответствие', 'соответствовать', 'root'),
    ('завет', 'завещать', 'root'),
    ('ведать', 'ведомство', 'root'),
    ('весть', 'известить', 'root'),
    ('сторона', 'сторонник', 'root'),
    ('часть', 'частичный', 'root'),
    ('счастье', 'счастливый', 'root'),
    ('сладкий', 'сладость', 'root'),
    ('солод', 'солодить', 'root'),
    ('трава', 'травяной', 'root'),
    ('отрава', 'отравить', 'root'),
    ('ключ', 'ключица', 'root'),
    ('включать', 'выключить', 'root'),
    ('город', 'городской', 'root'),
    ('ряд', 'рядовой', 'root'),
    ('сказать', 'рассказать', 'root'),
    ('дело', 'деловой', 'root'),
    ('суд', 'судить', 'root'),
    ('класть', 'вклад', 'root'),
    ('сад', 'сажать', 'root'),
    ('сидеть', 'сиденье', 'root'),
    ('держать', 'поддержка', 'root'),
    ('знать', 'знание', 'root'),
    ('вертеть', 'отвертка', 'root'),
    ('тяга', 'тянуть', 'root'),
    ('катить', 'прокат', 'root'),
    ('качать', 'качели', 'root'),
    ('течь', 'течение', 'root'),
    ('точить', 'точилка', 'root'),
    ('рвать', 'взрыв', 'root'),
    ('рыть', 'ров', 'root'),
    ('рубить', 'порубка', 'root'),
    ('рубль', 'рублевый', 'root'),
    ('влечь', 'увлечение', 'root'),
    ('волокно', 'волокнистый', 'root'),
    ('бог', 'божественный', 'root'),
    ('богатый', 'богатство', 'root'),
    ('бедный', 'бедствие', 'root'),
    ('победа', 'победить', 'root'),
    ('убеждать', 'убеждение', 'root'),
    ('стол', 'столица', 'root'),
    ('постель', 'стелить', 'root'),
    ('зреть', 'зрение', 'root'),
    ('зеркало', 'зеркальный', 'root'),
    ('мороз', 'замерзать', 'root'),
    ('мерзкий', 'мерзость', 'root'),
    ('вес', 'весить', 'root'),
    ('вешать', 'вешалка', 'root'),
    ('лить', 'заливать', 'root'),
    ('влияние', 'влиять', 'root'),
    ('расти', 'растение', 'root'),
    ('роща', 'рощица', 'root'),
    ('вязать', 'связь', 'root'),
    ('вязкий', 'вязкость', 'root'),
    ('голос', 'голосовать', 'root'),
    ('гласный', 'гласность', 'root'),
    ('ступать', 'ступень', 'root'),
    ('преступление', 'преступник', 'root'),
    ('простоять', 'застой', 'root'),
    ('оставить', 'остальной', 'root'),
    ('остаться', 'остаток', 'root'),
    ('состоять', 'состояние', 'root'),
    ('поставить', 'поставка', 'root'),
    ('состав', 'составить', 'root'),
    ('взять', 'поднять', 'root'),
    ('понять', 'понятие', 'root'),
    ('нанять', 'наем', 'root'),
    ('дать', 'отдать', 'root'),
    ('дар', 'одарить', 'root'),
    ('задача', 'задавать', 'root'),
    ('продать', 'продажа', 'root'),
    ('бить', 'забить', 'root'),
    ('битва', 'боец', 'root'),
    ('нести', 'поднести', 'root'),
    ('тереть', 'натереть', 'root'),
    ('стирать', 'стирка', 'root'),
    ('жить', 'жизнь', 'root'),
    ('оживить', 'оживление', 'root'),
    ('резать', 'отрезать', 'root'),
    ('пить', 'выпить', 'root'),
    ('быть', 'бытие', 'root'),
    ('раб', 'рабство', 'root'),
    ('душа', 'душевный', 'root'),
    ('дух', 'духовный', 'root'),
    ('много', 'многий', 'root'),
    ('умножение', 'умножить', 'root'),
    ('цель', 'целевой', 'root'),
    ('хлопать', 'прихлопнуть', 'root'),
    ('хлопок', 'хлопковый', 'root'),
    ('трепать', 'истрепать', 'root'),
    ('трепет', 'трепетать', 'root'),
    ('бред', 'бредовой', 'root'),
    ('брод', 'бродяга', 'root'),
    ('сват', 'свадьба', 'root'),
    ('свой', 'усвоить', 'root'),
    ('опыт', 'испытать', 'root'),
    ('окно', 'окошко', 'root'),
    ('чудо', 'чудесный', 'root'),
    # «погреб» is also a form of the verb «погрести».
    ('погреб', 'погребение', 'root'),
    ('орёл', 'Орёл', 'exact'),
    ('мёд', 'мед', 'exact'),
    ('евросоюз', 'ЕС', 'alias'),
    ('университет', 'универ', 'alias'),
    ('фотография', 'фото', 'alias'),
    ('килограмм', 'кг', 'alias'),
    ('километр', 'км', 'alias'),
    ('миллион', 'млн', 'alias'),
    ('французский', 'фр', 'alias'),
    ('французский', 'франц', 'alias'),
    ('английский', 'англ', 'alias'),
    ('немецкий', 'нем', 'alias'),
    ('латинский', 'лат', 'alias'),
    ('китайский', 'кит', 'alias'),
    ('рим', 'римской', 'root'),
    ('рим', 'римский', 'root'),
    ('рим', 'римлянин', 'root'),
    ('париж', 'парижский', 'root'),
    ('москва', 'московский', 'root'),
    ('китай', 'китайский', 'root'),
    ('франция', 'французский', 'root'),
    ('украина', 'украинский', 'root'),
    ('австралия', 'австралийский', 'root'),
    ('армения', 'армянский', 'root'),
    ('беларусь', 'белорусский', 'root'),
    ('бразилия', 'бразильский', 'root'),
    ('грузия', 'грузинский', 'root'),
    ('израиль', 'израильский', 'root'),
    ('иран', 'иранский', 'root'),
    ('ирак', 'иракский', 'root'),
    ('канада', 'канадский', 'root'),
    ('казахстан', 'казахстанский', 'root'),
    ('крым', 'крымский', 'root'),
    ('мексика', 'мексиканский', 'root'),
    ('польша', 'польский', 'root'),
    ('сибирь', 'сибирский', 'root'),
    ('турция', 'турецкий', 'root'),
    ('кавказ', 'кавказский', 'root'),
    ('африка', 'африканский', 'root'),
    ('англия', 'английский', 'root'),
    ('россия', 'российский', 'root'),
    ('байкал', 'байкальский', 'root'),
    ('толстой', 'толстовский', 'root'),
    ('петербург', 'петербургский', 'root'),
    ('20', 'XX', 'alias'),
    ('19', 'XIX', 'alias'),
    ('1', 'I', 'alias'),
    ('99', 'XCIX', 'alias'),
)

FALSE_CASES = (
    'пчела', 'пчеловод',
    'пчела', 'пчеловодство',
    'мёд', 'медоносный',
    'мед', 'медоносный',
    'вода', 'водопад',
    'лес', 'лесостепь',
    'земля', 'землетрясение',
    'вода', 'водить',
    'лето', 'лететь',
    'семь', 'семья',
    'полный', 'половина',
    'соты', 'сотня',
    'страна', 'странный',
    'крупа', 'крупный',
    'чета', 'чётный',
    'душа', 'душный',
    'год', 'годный',
    'свет', 'светский',
    'мать', 'матка',
    'гора', 'горний',
    'голова', 'глава',
    'голова', 'уголовный',
    'голова', 'главный',
    'белый', 'белок',
    'белый', 'белка',
    'север', 'северо-западный',
    'идти', 'ходить',
    'пчила', 'пчела',
    'bee', 'пчела',
    'Moscow', 'Москва',
    'краса', 'красный',
    'плоть', 'плотный',
    'друг', 'другой',
    'лебеда', 'лебединый',
    'червь', 'червовый',
    'колос', 'колосник',
    'сырник', 'сырость',
    'ворон', 'воронение',
    'слава', 'слово',
    'слово', 'условие',
    'мел', 'мельник',
    'мел', 'молоть',
    'мелкий', 'молоть',
    'печка', 'печаль',
    'печка', 'обеспечить',
    'печка', 'пещера',
    'печка', 'печень',
    'раз', 'образ',
    'сразу', 'образ',
    'зараза', 'раз',
    'личинка', 'отличие',
    'брачный', 'забраться',
    'необходимый', 'выходить',
    'уставный', 'уставать',
    'устать', 'стоять',
    'достаточный', 'остаться',
    'цветок', 'бесцветный',
    'самец', 'самый',
    'жаль', 'жальник',
    'вкус', 'искусство',
    'вкус', 'искус',
    'стачка', 'такой',
    'мещанин', 'место',
    'простой', 'простить',
    'простой', 'простыня',
    'положить', 'пологость',
    'обруч', 'обручение',
    'стоять', 'уставный',
    'стоять', 'достаточный',
    'образ', 'выражать',
    'разить', 'выражать',
    'отражатель', 'поражать',
    'запас', 'опасность',
    'запас', 'пастух',
    'пастух', 'спасти',
    'плести', 'плот',
    'плот', 'плотник',
    'грести', 'гроб',
    'гроб', 'погреб',
    'погребок', 'погребение',
    'маревый', 'мара',
    'мир', 'море',
    'мир', 'умирать',
    'морить', 'умирать',
    'мор', 'смерть',
    'выморить', 'вымирать',
    'замирать', 'умирать',
    'замереть', 'умереть',
    'уморительный', 'уморить',
    'морилка', 'морить',
    'справка', 'направление',
    'справка', 'правительство',
    'правительство', 'исправить',
    'заправка', 'приправа',
    'расправа', 'расправить',
    'совет', 'ответ',
    'совет', 'ответственность',
    'ответ', 'ответственность',
    'совет', 'привет',
    'ответ', 'завет',
    'привет', 'вещать',
    'совет', 'вечный',
    'соответствие', 'ответ',
    # Surface «весть» is also an imperative of «ведать»; close lemma neighbours instead.
    'ведать', 'известить',
    'разведка', 'повесть',
    'сведущий', 'вестник',
    'ведать', 'совесть',
    'ведать', 'ведьма',
    'ведать', 'невеста',
    'известить', 'ведьма',
    'известить', 'невеста',
    'сторона', 'страна',
    'страна', 'страница',
    'сторона', 'пространство',
    'часть', 'счастье',
    'часть', 'участок',
    'часть', 'участие',
    'сладкий', 'солод',
    'трава', 'отрава',
    'трава', 'травить',
    'ключ', 'включать',
    'ключ', 'приключение',
    'включать', 'приключение',
    'город', 'гражданин',
    'город', 'огород',
    'город', 'награда',
    'город', 'град',
    'ряд', 'наряд',
    'порядок', 'снаряд',
    'сказать', 'указать',
    'сказать', 'наказать',
    # Surface «дело» is also a rare form of «деть».
    'деловой', 'одеть',
    'изделие', 'надеть',
    'дело', 'действие',
    'суд', 'ссуда',
    'суд', 'рассудок',
    'класть', 'склад',
    'сад', 'сидеть',
    'сад', 'осада',
    'сидеть', 'седло',
    'держать', 'дергать',
    'держать', 'драть',
    'знать', 'знак',
    'знать', 'значение',
    'знак', 'знакомый',
    'вертеть', 'вернуть',
    'тяга', 'тяжелый',
    'катить', 'качать',
    'течь', 'точить',
    'восток', 'течение',
    'рвать', 'рыть',
    # Surface «взрыв» also parses against «взрыть».
    'взорвать', 'рыть',
    'взрывной', 'рытвина',
    'рубить', 'рубль',
    'рубить', 'рубашка',
    'влечь', 'волокно',
    'влечь', 'волочь',
    'бог', 'богатый',
    'бедный', 'убеждать',
    'бедный', 'победа',
    'стол', 'постель',
    'зеркало', 'зреть',
    'мороз', 'мерзкий',
    'вес', 'вешать',
    'лить', 'влияние',
    'расти', 'роща',
    'вязать', 'вязкий',
    'голос', 'гласный',
    'голос', 'согласие',
    'ступить', 'преступление',
    'простой', 'остальные',
    'простой', 'состоит',
    'остальные', 'состоит',
    'стоять', 'стать',
    'стоять', 'ставить',
    'стоять', 'остальной',
    'стоять', 'состоять',
    'стать', 'ставить',
    'оставить', 'остановить',
    'состав', 'состоять',
    'ставить', 'поставить',
    'три', 'строить',
    'водить', 'вождь',
    'водить', 'сводник',
    'водить', 'завод',
    'водить', 'производство',
    'мирный', 'мирской',
    'мирный', 'смирный',
    'общий', 'общество',
    'общий', 'общаться',
    'один', 'одинаковый',
    'один', 'одинокий',
    'средний', 'средство',
    'длина', 'долина',
    'удар', 'ударение',
    'равный', 'ровный',
    'равный', 'равнина',
    'верх', 'совершить',
    'гад', 'гадкий',
    'следить', 'следствие',
    'следить', 'наследство',
    'следить', 'преследовать',
    'следствие', 'следовательно',
    'взять', 'понять',
    'взять', 'нанять',
    'взять', 'емкость',
    'дать', 'дар',
    'дать', 'задача',
    'дать', 'продать',
    'бить', 'битва',
    'нести', 'износ',
    'тереть', 'стирать',
    'жить', 'оживить',
    'резать', 'резкий',
    'пить', 'пьяный',
    'пить', 'поить',
    'валить', 'валик',
    'мести', 'метель',
    'плавать', 'плавить',
    'гореть', 'горе',
    'мера', 'пример',
    'ткать', 'точка',
    'быть', 'быт',
    'быть', 'забыть',
    'кидать', 'скидка',
    'речь', 'срок',
    'пустить', 'отпуск',
    'гнуть', 'гибнуть',
    'делить', 'отдел',
    'говорить', 'уговор',
    'колоть', 'кол',
    'двигать', 'движимость',
    'строение', 'настроение',
    'работа', 'раб',
    'белый', 'белье',
    'есть', 'яд',
    'учить', 'наука',
    'толкать', 'толокно',
    'свет', 'свеча',
    'просить', 'вопрос',
    'искать', 'иск',
    'верить', 'проверить',
    'хватать', 'хватить',
    'мысль', 'промышленность',
    'топать', 'утопать',
    'нож', 'заноза',
    'крутить', 'крутой',
    'душа', 'дух',
    'дух', 'воздух',
    'думать', 'дума',
    'полный', 'выполнить',
    'добавить', 'забава',
    'ум', 'уметь',
    'любить', 'любоваться',
    'сила', 'насилие',
    'целый', 'исцелить',
    'целый', 'цель',
    'много', 'умножение',
    'много', 'множество',
    'целовать', 'цель',
    'хлопать', 'хлопок',
    'хлопать', 'хлопоты',
    'трепать', 'трепет',
    'лупить', 'лупа',
    'бред', 'брод',
    'сват', 'свой',
    'сукно', 'сучить',
    'особа', 'способ',
    'помнить', 'мнение',
    'палить', 'опала',
    'острый', 'острог',
    'тушить', 'тухлый',
    'опыт', 'пытка',
    'липа', 'липкий',
    'окно', 'очки',
    'мигать', 'минуть',
    'половина', 'половой',
    'сдоба', 'удобный',
    'баба', 'бабочка',
    'вдох', 'дохлый',
    'основа', 'сновать',
    'чудо', 'причуда',
    'начать', 'начальник',
    'тупой', 'тупик',
    'пух', 'опушка',
    'обман', 'манить',
    'рим', 'париж',
    'москва', 'китай',
    'россия', 'русский',
    'германия', 'немецкий',
)


class SemanticsTests(SimpleTestCase):
    def test_required_matches(self):
        for guess, target, reason in TRUE_CASES:
            self.assertTrue(opens(guess, target), f'{guess}/{target} stayed closed')
            self.assertEqual(explain(guess, target), reason, f'{guess}/{target}')
            self.assertTrue(opens(target, guess), f'{target}/{guess}')

    def test_required_closures(self):
        pairs = list(zip(FALSE_CASES[0::2], FALSE_CASES[1::2]))
        for guess, target in pairs:
            self.assertFalse(
                opens(guess, target),
                f'{guess}/{target} opened as {explain(guess, target)}',
            )
            self.assertFalse(opens(target, guess), f'{target}/{guess}')

    def test_service_words_are_not_root_guesses(self):
        self.assertTrue(initially_open('бы'))
        self.assertTrue(initially_open('в'))
        self.assertTrue(initially_open('и'))
        self.assertTrue(initially_open('не'))
        self.assertFalse(opens('бы', 'быть'))
        self.assertFalse(opens('быть', 'бы'))
        self.assertFalse(initially_open('он'))
        self.assertFalse(initially_open('пчела'))
        self.assertFalse(initially_open('равно'))

    def test_generic_is_exact_only(self):
        self.assertEqual(explain('Apis', 'apis'), 'exact')
        self.assertEqual(explain('DNA', 'dna'), 'exact')
        self.assertFalse(opens('Apis', 'пчела'))
        self.assertFalse(opens('hello-world', 'hello'))
        self.assertEqual(explain('α', 'Α'), 'exact')

    def test_compound_order_does_not_matter_but_a_repeat_does(self):
        self.assertTrue(opens('белоснежный', 'снежно-белый'))
        self.assertFalse(opens('один', 'один-одинехонек'))

    def test_relations_are_not_transitive(self):
        # Alias does not pull in the other word's root family.
        self.assertEqual(explain('фото', 'фотография'), 'alias')
        self.assertFalse(opens('фото', 'фотограф'))
        self.assertFalse(opens('универ', 'университетский'))
        self.assertEqual(explain('фр', 'французский'), 'alias')
        self.assertEqual(explain('англ', 'английский'), 'alias')
        self.assertFalse(opens('фр', 'француз'))
        self.assertFalse(opens('англ', 'англичанин'))
        # Grammar does not pull in a later root neighbour.
        self.assertEqual(explain('один', 'первый'), 'grammar')
        self.assertFalse(opens('один', 'первенец'))
        # Irregular grammar does not continue into an unrelated root.
        self.assertEqual(explain('хороший', 'лучше'), 'lexeme')
        self.assertFalse(opens('кг', 'километровый'))
        self.assertFalse(opens('км', 'килограммовый'))

    def test_arabic_and_roman_numerals_alias(self):
        self.assertEqual(explain('20', 'XX'), 'alias')
        self.assertEqual(explain('19', 'XIX'), 'alias')
        self.assertEqual(explain('1', 'I'), 'alias')
        self.assertEqual(explain('4', 'IV'), 'alias')
        self.assertEqual(explain('99', 'XCIX'), 'alias')
        self.assertFalse(opens('20', 'XIX'))
        self.assertFalse(opens('20', 'двадцать'))
        self.assertFalse(opens('I', 'II'))

    def test_split_subfamilies_are_disjoint(self):
        from games.censorly.lexical.semantic_splits import CLUSTERS
        by_name = {}
        seen = {}
        for name, lemmas in CLUSTERS:
            self.assertNotIn(name, by_name)
            by_name[name] = lemmas
            for lemma in lemmas:
                self.assertNotIn(lemma, seen, f'{lemma} in {seen.get(lemma)} and {name}')
                seen[lemma] = name
        closed = (
            ('sense:beauty', 'sense:red'),
            ('sense:glory', 'sense:word'),
            ('sense:word', 'sense:condition'),
            ('sense:bloom', 'sense:color'),
            ('sense:chalk', 'sense:mill'),
            ('sense:taste', 'sense:art'),
            ('sense:care', 'sense:sadness'),
            ('sense:take', 'sense:understand'),
            ('sense:give', 'sense:gift'),
            ('sense:beat', 'sense:battle'),
            ('sense:live', 'sense:animate'),
            ('sense:work', 'sense:slave'),
            ('sense:soul', 'sense:spirit'),
            ('sense:many', 'sense:multiply'),
            ('sense:whole', 'sense:goal'),
            ('sense:clap', 'sense:cotton'),
            ('sense:thrash', 'sense:tremor'),
            ('sense:delirium', 'sense:ford'),
            ('sense:experience', 'sense:torture'),
            ('sense:window', 'sense:eye'),
            ('sense:wonder', 'sense:whim'),
        )
        for left_name, right_name in closed:
            left = by_name[left_name][0]
            right = by_name[right_name][0]
            self.assertFalse(opens(left, right), f'{left}/{right}')
        self.assertTrue(opens('красивый', 'красота'))
        self.assertTrue(opens('красный', 'краска'))
        self.assertTrue(opens('мельник', 'молоть'))
        self.assertTrue(opens('цветок', 'цвести'))
        self.assertTrue(opens('бесцветный', 'цветной'))
        self.assertTrue(opens('простой', 'упростить'))
        self.assertTrue(opens('простить', 'прощение'))

    def test_alternative_root_readings_are_not_a_compound(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        mary = structures_of('марь')
        self.assertEqual(set(mary), {('sense:haze',), ('sense:plant',)})
        self.assertNotIn(('sense:haze', 'sense:plant'), mary)
        self.assertEqual(structures_of('маревый'), (('sense:haze',),))
        self.assertNotIn(('sense:plant',), structures_of('маревый'))
        self.assertTrue(opens('марь', 'маревый'))
        self.assertFalse(opens('маревый', 'мара'))
        beekeeper = assignment_of('пчеловод')
        self.assertEqual(beekeeper['structures'], ())
        self.assertEqual(beekeeper['source'], 'UNRESOLVED')
        self.assertFalse(opens('пчела', 'пчеловод'))
        snow = assignment_of('белоснежный')
        self.assertEqual(snow['source'], 'UNIQUE_SPELLING_FALLBACK')
        self.assertTrue(snow['structures'])
        self.assertTrue(all(len(item) >= 2 for item in snow['structures']))

    def test_spelling_does_not_join_numbered_families(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        sea = structures_of('мир')
        self.assertTrue(sea)
        self.assertTrue(all('мир²' not in item[0] and 'мор¹' not in item[0] for item in sea))
        self.assertFalse(opens('мир', 'море'))
        self.assertFalse(opens('мир', 'умирать'))
        self.assertTrue(opens('умирать', 'смерть'))
        self.assertTrue(opens('мир', 'мирный'))
        self.assertTrue(opens('море', 'морской'))
        self.assertFalse(opens('вода', 'водить'))
        chrism = assignment_of('миро')
        self.assertEqual(chrism['source'], 'SEMANTIC_SPLIT')
        self.assertEqual(chrism['structures'], (('sense:chrism',),))
        ambiguous = assignment_of('веризм')
        self.assertEqual(ambiguous['source'], 'UNRESOLVED')
        self.assertEqual(ambiguous['structures'], ())
        self.assertGreater(len(ambiguous['refused']), 1)
        self.assertFalse(opens('веризм', 'верить'))

    def test_compounds_keep_full_structures_only(self):
        from games.censorly.lexical.rootbank import (
            _ATOMIC_LEXICALIZED, _PROVENANCE, _SENSE, assignment_of, structures_of,
        )

        self.assertIn(fold('красивенький'), _ATOMIC_LEXICALIZED)
        self.assertIn(fold('мелюзга'), _ATOMIC_LEXICALIZED)
        self.assertIn(fold('оживление'), _ATOMIC_LEXICALIZED)
        beauty = assignment_of('красивенький')
        self.assertEqual(beauty['source'], 'ATOMIC_LEXICALIZED')
        self.assertEqual(beauty['structures'], (('sense:beauty',),))
        self.assertTrue(opens('красивенький', 'красивый'))
        small = assignment_of('мелюзга')
        self.assertEqual(small['source'], 'ATOMIC_LEXICALIZED')
        self.assertEqual(small['structures'], (('sense:small',),))
        animate = assignment_of('оживление')
        self.assertEqual(animate['source'], 'ATOMIC_LEXICALIZED')
        self.assertEqual(animate['structures'], (('sense:animate',),))
        self.assertTrue(opens('оживление', 'оживить'))
        for lemma, info in _PROVENANCE.items():
            structs = structures_of(lemma)
            self.assertLessEqual(len(structs), 32, lemma)
            if info['source'] in ('UNRESOLVED', 'UNRESOLVED_TOO_AMBIGUOUS'):
                self.assertEqual(structs, (), lemma)
            if info['source'] == 'ATOMIC_LEXICALIZED':
                self.assertIn(lemma, _ATOMIC_LEXICALIZED)
                self.assertTrue(all(
                    len(item) == 1 and item[0].startswith('sense:') for item in structs
                ))
            token = (info['detail'] or '').split()[-1] if info['detail'] else ''
            if '+' not in token:
                continue
            parts = token.split('+')
            self.assertGreaterEqual(len(parts), 2)
            self.assertNotIn(info['source'], ('SEMANTIC_SPLIT', 'MANUAL_MULTI_READING'), lemma)
            if lemma in _ATOMIC_LEXICALIZED:
                continue
            for item in structs:
                self.assertEqual(len(item), len(parts), lemma)
                self.assertFalse(any(part.startswith('sense:') for part in item), lemma)
            if lemma in _SENSE:
                for item in structs:
                    self.assertFalse(any(part in _SENSE[lemma] for part in item), lemma)

    def test_split_families_leave_no_bridge_lemma(self):
        from games.censorly.lexical.core import fold
        from games.censorly.lexical.rootbank import _KUZ_GROUPS, _KUZ_LEMMAS, _SENSE
        from games.censorly.lexical.russian.compiler import (
            _families, _lines, _norm, _strip_note,
        )

        families = _families(_KUZ_GROUPS)
        wanted = {
            'раж¹|раз¹', 'пас²', 'плач³|плес¹|плет|плот¹|плоч',
            'граб¹|греб|грес|гроб', 'мар³', 'прав', 'вет¹|веч²|вещ²',
            'вед¹|веж|вежд¹|вест|вещ¹', 'сторон|стран', 'част¹|чащ¹',
            'слад|слажд|сласт|слащ|солаж|солод|солож|солощ', 'трав',
            'клюк¹|ключ¹|клюш',
            'гораж|город|горож|град¹|гражд', 'ряд|ряж¹', 'каж¹|кажд¹|каз¹',
            'де(j)|де', 'суд¹|суж|сужд', 'кла|клад|клаж|клас',
            'сад|саж|сажд|сед¹|сед|сес|сид|сиж¹|сяд',
            'дер|дерг|держ|дир|дор|дорог³|др', 'зна(j)',
            'вер²|верет²|верт|верч', 'тя|тяг|тяж|тяз',
            'мар²|мер²|мер|мир²|мор¹',
            'кат¹|кач¹', 'тач¹|тек|теч|ток¹|точ¹', 'рв|ров¹|ры', 'руб',
            'влач|влек|влеч|волак|волок|волоч', 'бог|бож', 'бед|бежд',
            'сл²|стел|стил|стл|стол', 'зар|зер|зир|зор|зр',
            'мерз|мораж|морож|мороз|мраз', 'вес¹|веш¹',
            'ле(j)|ли|ло(j)|ль²', 'рас|ращ|рос²|рощ', 'вяж|вяз²',
            'глас|глаш|голос', 'ступ¹',
            'ста(j)|сто(j)',
            'важ²|важд|вед²|вес²|вод¹|вож¹|вожд', 'мир¹', 'общ', 'один|одн',
            'серед|сред', 'дл|дол²', 'удар', 'рав|ров²', 'верх|верш¹',
            'гад²|гаж', 'тр¹', 'след|слеж',
        }
        missing = []
        for line in _lines(_KUZ_LEMMAS):
            raw_lemma, raw_root = line.split('\t', 1)
            lemma = fold(_strip_note(raw_lemma))
            root = _norm(raw_root.strip())
            family = families.get(root, root)
            if family in wanted and lemma not in _SENSE:
                missing.append(f'{family}:{lemma}')
        self.assertEqual(missing, [])


class LexicalGameplayTests(TestCase):
    """Flag on changes masks through apply_guess. Flag off stays on lemma match."""

    BODY = (
        'Пчёлами полон улей. Пчелиный мёд сладок. Пчеловод работает. '
        'Медовый вкус рядом. Медоносный цветок. Белки лежат. '
        'Выходить рано. Три окна. Испарять воду. Странный случай. '
        'Матка пчелы. Университет открыт. Фотография ясна. Просто факт.'
    )

    def _task(self):
        from games.tests.test_censorly import _make_puzzle_task
        return _make_puzzle_task(title='Корзина', body=self.BODY)

    def _revealed(self, game, task, user):
        import json
        from games.models import ChainTaskState
        row = ChainTaskState.objects.get(
            task=task, game=game, game_mode='general',
            user=user, team=None, anon_key=None,
        )
        return set(json.loads(row.state).get('revealed_lemmas') or [])

    def _surfaces_for_ids(self, puzzle, ids):
        by_id = {
            tok['id']: tok
            for tok in (puzzle.get('title_tokens') or []) + (puzzle.get('body_tokens') or [])
        }
        return [fold(by_id[item].get('surface') or '') for item in ids]

    def _open_texts(self, result):
        from games.censorly.lexical.core import fold
        return {
            fold(tok.get('text') or '')
            for tok in result['body_tokens']
            if tok.get('revealed') and tok.get('text')
        }

    def test_flag_off_does_not_open_root_neighbours(self):
        from django.test import override_settings
        from games.censorly.play import apply_guess
        game, task, _hash, puzzle = self._task()
        user = User.objects.create_user('cz_lex_off', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=False):
            result = apply_guess(game=game, task=task, word='пчела', user=user)
        revealed = self._revealed(game, task, user)
        texts = self._open_texts(result)
        self.assertEqual(
            self._surfaces_for_ids(puzzle, result['guesses'][-1]['token_ids']),
            [fold('Пчёлами'), fold('пчелы')],
        )
        self.assertIn(fold('пчёлами'), texts)
        self.assertNotIn(fold('пчелиный'), texts)
        self.assertNotIn(fold('пчеловод'), texts)
        self.assertTrue(revealed)
        self.assertNotIn(fold('пчелиный'), {fold(item) for item in revealed})
        from games.models import StatisticsEvent
        self.assertFalse(StatisticsEvent.objects.filter(
            kind=StatisticsEvent.KIND_CENSORLY_LEXICAL_GUESS,
        ).exists())

    def test_flag_on_updates_state_and_masks(self):
        from django.test import override_settings
        from games.censorly.normalize import lemma_of
        from games.censorly.play import apply_guess, get_play_state
        game, task, _hash, puzzle = self._task()
        user = User.objects.create_user('cz_lex_on', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=True):
            before = get_play_state(game=game, task=task, user=user)
            opened = self._open_texts(before)
            self.assertIn(fold('просто'), opened)
            bee = apply_guess(game=game, task=task, word='пчела', user=user)
            self.assertEqual(
                self._surfaces_for_ids(puzzle, bee['guesses'][-1]['token_ids']),
                [fold('Пчёлами'), fold('Пчелиный'), fold('пчелы')],
            )
            texts = self._open_texts(bee)
            self.assertIn(fold('пчёлами'), texts)
            self.assertIn(fold('пчелиный'), texts)
            self.assertNotIn(fold('пчеловод'), texts)
            from games.models import StatisticsEvent
            logged = StatisticsEvent.objects.get(
                kind=StatisticsEvent.KIND_CENSORLY_LEXICAL_GUESS,
                payload__guess='пчела',
            )
            opened = {fold(item['lemma']) for item in logged.payload['opened']}
            rejected = {
                (fold(item['lemma']), item['reason'])
                for item in logged.payload['rejected']
            }
            self.assertIn(fold('пчелиный'), opened)
            self.assertIn(fold('пчела'), opened)
            self.assertNotIn(fold('пчеловод'), opened)
            self.assertNotIn(fold('пчеловод'), {lemma for lemma, _reason in rejected})
            honey = apply_guess(game=game, task=task, word='мёд', user=user)
            honey_texts = self._open_texts(honey)
            self.assertIn(fold('медовый'), honey_texts)
            self.assertNotIn(fold('медоносный'), honey_texts)
            protein = apply_guess(game=game, task=task, word='белок', user=user)
            self.assertIn(fold('белки'), self._open_texts(protein))
            squirrel = apply_guess(game=game, task=task, word='белка', user=user)
            self.assertIn(fold('белки'), self._open_texts(squirrel))
            walk = apply_guess(game=game, task=task, word='ходить', user=user)
            self.assertIn(fold('выходить'), self._open_texts(walk))
            triple = apply_guess(game=game, task=task, word='третий', user=user)
            self.assertIn(fold('три'), self._open_texts(triple))
            steam = apply_guess(game=game, task=task, word='пара', user=user)
            self.assertNotIn(fold('испарять'), self._open_texts(steam))
            country = apply_guess(game=game, task=task, word='страна', user=user)
            self.assertNotIn(fold('странный'), self._open_texts(country))
            mother = apply_guess(game=game, task=task, word='мать', user=user)
            self.assertNotIn(fold('матка'), self._open_texts(mother))
            alias = apply_guess(game=game, task=task, word='универ', user=user)
            self.assertIn(fold('университет'), self._open_texts(alias))
            photo = apply_guess(game=game, task=task, word='фото', user=user)
            self.assertIn(fold('фотография'), self._open_texts(photo))
        revealed = self._revealed(game, task, user)
        self.assertIn(lemma_of('пчелиный'), revealed)
        self.assertNotIn(lemma_of('пчеловод'), revealed)

    def test_flag_on_rejects_words_that_are_rendered_open(self):
        from django.test import override_settings
        from games.censorly.play import apply_guess
        from games.models import ChainTaskState

        game, task, _hash, _puzzle = self._task()
        user = User.objects.create_user('cz_lex_initially_open', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=True):
            result = apply_guess(game=game, task=task, word='просто', user=user)

        self.assertEqual(result['status'], 'initially_open')
        self.assertEqual(result['guess_word'], 'просто')
        self.assertEqual(result['attempts'], 0)
        self.assertFalse(ChainTaskState.objects.filter(task=task, user=user).exists())

    def test_mir_does_not_reveal_sea(self):
        from django.test import override_settings
        from games.censorly.play import apply_guess
        from games.tests.test_censorly import _make_puzzle_task

        game, task, _hash, _puzzle = _make_puzzle_task(
            title='Корзина',
            body='Море шумит. Мирный договор. Умереть рано.',
        )
        user = User.objects.create_user('cz_mir', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=True):
            sea = apply_guess(game=game, task=task, word='мир', user=user)
            texts = self._open_texts(sea)
            self.assertIn(fold('мирный'), texts)
            self.assertNotIn(fold('море'), texts)
            self.assertNotIn(fold('умереть'), texts)
            death = apply_guess(game=game, task=task, word='умирать', user=user)
            self.assertIn(fold('умереть'), self._open_texts(death))


class ReviewedHomonymClusterTests(SimpleTestCase):
    def test_reviewed_homonym_clusters_stay_apart(self):
        groups = (
            ('несколько', 'количество'),
            ('питаются', 'воспитательный'),
            ('природа', 'родственник'),
            ('список', 'описание'),
            ('сложный', 'предполагают', 'выложенное', 'располагается'),
            ('взятка', 'сняты', 'преимущество', 'иметь', 'вынули'),
            ('налог', 'предложение', 'предположение', 'располагавший', 'положение'),
            ('перелетные', 'года'),
            ('конец', 'закон'),
            ('налог', 'обложка', 'приложение'),
            ('запад', 'нападение'),
            ('находится', 'похоже'),
            ('бой', 'убит'),
            ('дело', 'понедельник'),
            ('собрание', 'выбор'),
            ('Путин', 'путей'),
        )
        for group in groups:
            for index, left in enumerate(group):
                for right in group[index + 1:]:
                    self.assertFalse(opens(left, right), f'{left} / {right}')
                    self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_additional_safe_family_splits_stay_apart(self):
        pairs = (
            ('гладкий', 'голодный'),
            ('глодать', 'голодать'),
            ('гнилой', 'гнойный'),
            ('жатва', 'ужин'),
            ('жирный', 'жрать'),
            ('кликнуть', 'кличка'),
            ('крепкий', 'крепость'),
            ('творить', 'затвор'),
            ('чертить', 'чёрт'),
            ('хитрый', 'восхищение'),
            ('хищение', 'восхищение'),
            ('видеть', 'зависть'),
            ('велеть', 'воля'),
            ('кровь', 'крыша'),
            ('напечатать', 'впечатление'),
            ('круглый', 'кружиться'),
            ('дуб', 'дубить'),
            ('земля', 'заземлить'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_second_safe_family_split_batch_stays_apart(self):
        pairs = (
            ('гром', 'погром'),
            ('лазить', 'лестница'),
            ('ползти', 'полоз'),
            ('ползти', 'оползень'),
            ('строгий', 'строгать'),
            ('хлеб', 'хлебать'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_new_semantic_split_batch_stays_apart(self):
        pairs = (
            ('отдых', 'дыхание'),
            ('восторг', 'вторжение'),
            ('восторг', 'исторгнуть'),
            ('расторгнуть', 'торжество'),
            ('крест', 'крещение'),
            ('крестец', 'крестовина'),
            ('теребить', 'требовать'),
            ('требовать', 'истребить'),
            ('страсть', 'страх'),
            ('застращивать', 'застраховать'),
            ('высота', 'вышка'),
            ('порожний', 'испражнение'),
            ('упражнение', 'праздник'),
            ('праздник', 'упразднить'),
            ('трусики', 'натрусить'),
            ('труха', 'трусить'),
            ('изнанка', 'проникать'),
            ('никнуть', 'вникать'),
            ('пластина', 'распластать'),
            ('красть', 'красивый'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_followup_semantic_split_batch_stays_apart(self):
        pairs = (
            ('страдание', 'страсть'),
            ('пустой', 'пуща'),
            ('совок', 'засов'),
            ('засов', 'совать'),
            ('пять', 'пятиться'),
            ('пятиться', 'выпячивать'),
            ('спелый', 'поспешить'),
            ('поспешить', 'успешный'),
            ('доспехи', 'спелый'),
            ('кость', 'Кощей'),
            ('молоток', 'молотить'),
            ('рушить', 'рухлый'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_latest_semantic_split_batch_stays_apart(self):
        pairs = (
            ('хотеть', 'охотиться'),
            ('охота', 'похоть'),
            ('очерк', 'почерк'),
            ('почерк', 'зачеркнуть'),
            ('чувствовать', 'чуть'),
            ('жёлтый', 'желчь'),
            ('опрыскивать', 'прыщ'),
            ('похоронить', 'хранить'),
            ('пыль', 'опыление'),
            ('трещина', 'треск'),
            ('тонуть', 'затон'),
            ('затон', 'затопить'),
            ('дозваниваться', 'звенеть'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_additional_semantic_split_batch_stays_apart(self):
        pairs = (
            ('блеск', 'блесна'),
            ('гладкий', 'гладить'),
            ('грыжа', 'грызть'),
            ('чёрный', 'вчерне'),
            ('вчерне', 'очернить'),
            ('щепка', 'расщепить'),
            ('расщепить', 'прищепка'),
            ('отщепенец', 'щепка'),
            ('пошлый', 'пошлина'),
            ('пошлый', 'прошлый'),
            ('прошлый', 'пришлый'),
            ('пришлый', 'шествие'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_more_semantic_split_batch_stays_apart(self):
        pairs = (
            ('оплеуха', 'плевок'),
            ('владеть', 'властвовать'),
            ('власть', 'волость'),
            ('мужчина', 'замужество'),
            ('взбалтывать', 'болтовня'),
            ('грань', 'граница'),
            ('гранить', 'ограничение'),
            ('хлестать', 'захлестнуть'),
            ('трудиться', 'затруднение'),
            ('затруднение', 'сотрудничество'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_sleep_and_poke_followup_splits_stay_apart(self):
        pairs = (
            ('распарывать', 'порка'),
            ('порка', 'порочный'),
            ('пешня', 'пихать'),
            ('покой', 'беспокойство'),
            ('беспокоиться', 'покойник'),
            ('спать', 'усопший'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_semantic_review_batch_stays_apart(self):
        pairs = (
            ('верста', 'верстать'),
            ('верстать', 'сверстник'),
            ('строка', 'строчка'),
            ('твёрдый', 'твердить'),
            ('подтвердить', 'затвердеть'),
            ('наяву', 'заявление'),
            ('заявление', 'проявиться'),
            ('пустой', 'пустыня'),
            ('пустыня', 'пустяк'),
            ('смех', 'насмешка'),
            ('межа', 'между'),
            ('между', 'перемежать'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_careful_homonym_split_batch_stays_apart(self):
        pairs = (
            ('лес', 'леший'),
            ('прогалина', 'голый'),
            ('синий', 'синяк'),
            ('синий', 'синица'),
            ('кланяться', 'наклон'),
            ('поклон', 'склон'),
            ('щуп', 'щупальце'),
            ('щуплый', 'щупальце'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_careful_split_keeps_requested_bridges(self):
        self.assertTrue(opens('щуп', 'щуплый'))
        self.assertTrue(opens('льстить', 'обольстить'))


class ConfirmedSingleTokenRelationTests(SimpleTestCase):
    def test_reviewed_missing_root_assignments_are_structural(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        self.assertEqual(assignment_of('сенат')['source'], 'REVIEWED_OPAQUE_ROOT')
        self.assertEqual(structures_of('сенат'), (('tikh:сенат',),))
        self.assertEqual(structures_of('сенатор'), (('tikh:сенат',),))
        self.assertEqual(structures_of('сенатский'), (('tikh:сенат',),))
        self.assertEqual(structures_of('сенаторство'), (('tikh:сенат',),))

        self.assertEqual(assignment_of('иволговый')['source'], 'REVIEWED_DERIVED_ROOT')
        self.assertEqual(structures_of('иволговый'), (('fam:иволг',),))
        self.assertTrue(opens('сенат', 'сенатор'))
        self.assertTrue(opens('сенатор', 'сенатский'))
        self.assertTrue(opens('иволга', 'иволговый'))
        self.assertTrue(opens('иволга', 'иволговые'))

    def test_reviewed_assignments_keep_semantic_splits_closed(self):
        for left, right in (
            ('страна', 'странный'),
            ('крупа', 'крупный'),
            ('свет', 'светский'),
            ('мать', 'матка'),
            ('червь', 'червовый'),
            ('мара', 'маревый'),
            ('год', 'годный'),
            ('душа', 'душный'),
        ):
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')

class NewConfirmedSingleTokenRelationTests(SimpleTestCase):
    def test_confirmed_proper_pairs_are_bidirectional(self):
        from games.censorly.lexical.proper_names import PAIR_RELATIONS

        self.assertEqual(len(PAIR_RELATIONS), 55)
        for left, right in PAIR_RELATIONS:
            self.assertEqual(explain(left, right), 'proper', f'{left}/{right}')
            self.assertEqual(explain(right, left), 'proper', f'{right}/{left}')
            self.assertTrue(opens(left, right), f'{left}/{right}')
            self.assertTrue(opens(right, left), f'{right}/{left}')

    def test_proper_pairs_support_inflected_forms(self):
        cases = (
            ('Алжира', 'алжирскому'),
            ('Пекином', 'пекинского'),
            ('Венгрии', 'венгром'),
            ('Чехии', 'чехом'),
        )
        for left, right in cases:
            self.assertEqual(explain(left, right), 'proper', f'{left}/{right}')
            self.assertEqual(explain(right, left), 'proper', f'{right}/{left}')

    def test_proper_pairs_do_not_create_adjective_demonym_edges(self):
        from collections import defaultdict

        from games.censorly.lexical.proper_names import PAIR_RELATIONS
        from games.censorly.lexical.semantics import _PROPER_PAIRS, _proper_pair

        related = defaultdict(list)
        for left, right in PAIR_RELATIONS:
            related[left].append(right)
        for base, words in related.items():
            adjectives = [word for word in words if word.endswith(('ский', 'цкий', 'жский'))]
            demonyms = [
                word for word in words
                if word not in adjectives
            ]
            for adjective in adjectives:
                for demonym in demonyms:
                    pair = frozenset((adjective, demonym))
                    self.assertNotIn(pair, _PROPER_PAIRS)
                    self.assertFalse(_proper_pair(adjective, demonym))
                    self.assertFalse(_proper_pair(demonym, adjective))

    def test_new_pairs_are_not_accidentally_clustered(self):
        from games.censorly.lexical.proper_names import CLUSTERS, PAIR_RELATIONS

        clustered = {word for _name, words in CLUSTERS for word in words}
        for left, right in PAIR_RELATIONS:
            self.assertNotIn(left, clustered)
            self.assertNotIn(right, clustered)

    def test_measurement_aliases_are_bidirectional_and_non_transitive(self):
        aliases = (
            ('мл', 'миллилитр'),
            ('вт', 'ватт'),
            ('квт', 'киловатт'),
            ('мвт', 'мегаватт'),
            ('гц', 'герц'),
            ('втб', 'внешторгбанк'),
            ('авто', 'автомобиль'),
            ('факс', 'факсимиле'),
        )
        for short, full in aliases:
            self.assertEqual(explain(short, full), 'alias', f'{short}/{full}')
            self.assertEqual(explain(full, short), 'alias', f'{full}/{short}')
        for left, right in (
            ('мл', 'млн'),
            ('миллилитр', 'миллиграмм'),
            ('ватт', 'киловатт'),
            ('мегаватт', 'герц'),
            ('втб', 'автомобиль'),
            ('авто', 'факсимиле'),
            ('факс', 'внешторгбанк'),
        ):
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')

    def test_article_matcher_uses_explicit_proper_pairs(self):
        from games.censorly.lexical.match import _Pack, _relation

        self.assertEqual(
            _relation(_Pack('алжир'), _Pack('алжирский')),
            'proper',
        )
        self.assertEqual(
            _relation(_Pack('мл'), _Pack('миллилитр')),
            'alias',
        )
