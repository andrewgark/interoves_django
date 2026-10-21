"""Gameplay semantics: every possible reading, full root-set equality."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from games.censorly.lexical.core import fold
from games.censorly.lexical.semantics import (
    explain, initially_open, opens, readings_of,
)
from games.censorly.lexical.match import matching_lemmas
from games.censorly.lexical.tools.explain_pair import explain_pair
from games.censorly.tokenize import build_puzzle_payload, tokenize_text


def _has_sense_structure(lemma, sense):
    from games.censorly.lexical.rootbank import structures_of
    return any(
        sense in structure
        and any(not part.startswith('sense:') for part in structure)
        for structure in structures_of(lemma)
    )

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
    ('два', 'второй', 'grammar'),
    ('три', 'третий', 'root'),
    ('пять', 'пятый', 'root'),
    ('строить', 'страивать', 'root'),
    # Short masculine of «сырой» is also «сыр». Damp stays linked.
    ('сырой', 'сырость', 'root'),
    ('пологий', 'пологость', 'root'),
    ('устать', 'усталость', 'root'),
    ('стоять', 'стоянка', 'root'),
    ('стать', 'становиться', 'root'),
    ('ставить', 'ставка', 'root'),
    ('разить', 'поражать', 'root'),
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
    ('дело', 'деловой', 'root'),
    ('суд', 'судить', 'root'),
    ('класть', 'вклад', 'root'),
    ('сад', 'сажать', 'root'),
    ('сидеть', 'сиденье', 'root'),
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
    ('состоять', 'состояние', 'root'),
    ('поставить', 'поставка', 'root'),
    ('состав', 'составить', 'root'),
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
    ('опыт', 'испытать', 'root'),
    ('окно', 'окошко', 'root'),
    ('чудо', 'чудесный', 'root'),
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
    'отражать', 'поражать',
    'образ', 'образование',
    'один', 'однажды',
    'свой', 'усвоить',
    'гора', 'горний',
    'голова', 'глава',
    'голова', 'уголовный',
    'голова', 'главный',
    'белый', 'белок',
    'белый', 'белка',
    'север', 'северо-западный',
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
)


class SemanticsTests(SimpleTestCase):
    def test_belykh_uses_adjective_reading(self):
        readings = {(item.lemma, item.pos) for item in readings_of('белых')}
        self.assertIn(('белый', 'ADJF'), readings)
        self.assertNotIn(('белых', 'NOUN'), readings)
        self.assertTrue(opens('белый', 'белых'))
        self.assertTrue(opens('белых', 'белый'))

    def test_donskoy_adjective_variant_uses_existing_root(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        self.assertEqual(assignment_of('донский')['source'], 'REVIEWED_DERIVED_ROOT')
        self.assertEqual(structures_of('донский'), (('fam:дн²|дон',),))
        self.assertTrue(opens('донской', 'донский'))
        self.assertTrue(opens('донский', 'донской'))

    def test_lived_and_prosperous_meanings_stay_apart(self):
        self.assertFalse(opens('жил', 'зажиточной'))
        self.assertFalse(opens('зажиточной', 'жил'))
        self.assertTrue(opens('зажиточный', 'зажиточность'))

    def test_living_family_has_narrow_subclusters(self):
        self.assertTrue(opens('жилище', 'жилищный'))
        self.assertFalse(opens('жилище', 'живой'))
        self.assertTrue(opens('заживление', 'заживлять'))
        self.assertFalse(opens('заживление', 'жизнь'))
        self.assertTrue(opens('сожительство', 'сожительствовать'))
        self.assertFalse(opens('сожительство', 'жизнь'))
        self.assertTrue(opens('нажива', 'наживка'))
        self.assertFalse(opens('нажива', 'жить'))
        self.assertTrue(opens('выживание', 'выжить'))
        self.assertFalse(opens('выживание', 'жилище'))

    def test_mathematics_opaque_family(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        for lemma in ('математика', 'математик', 'математический'):
            self.assertEqual(assignment_of(lemma)['source'], 'REVIEWED_OPAQUE_ROOT')
            self.assertEqual(structures_of(lemma), (('tikh:математ',),))
        self.assertTrue(opens('математика', 'математический'))
        self.assertTrue(opens('математический', 'математика'))

    def test_surname_dal_opens_oblique_forms(self):
        for form in ('Даля', 'Далю', 'Далем'):
            self.assertTrue(opens('Даль', form))
            self.assertTrue(opens(form, 'Даль'))

    def test_chess_reviewed_family(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        for lemma in ('шахматы', 'шахматный', 'шахматист', 'шахматистка'):
            self.assertEqual(assignment_of(lemma)['source'], 'REVIEWED_DERIVED_ROOT')
            self.assertEqual(structures_of(lemma), (('root:шахмат',),))
        self.assertTrue(opens('шахматы', 'шахматная'))
        self.assertTrue(opens('шахматная', 'шахматы'))
        self.assertTrue(opens('шахматы', 'шахматистов'))
        self.assertFalse(opens('шахматы', 'мат'))
        self.assertFalse(opens('шах', 'шахматы'))

    def test_electrical_adverb_is_separate_from_electron(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        self.assertEqual(assignment_of('электрически')['source'], 'SEMANTIC_ROOT_ASSIGNMENT')
        self.assertTrue(_has_sense_structure('электрически', 'sense:electrical_adverb'))
        self.assertFalse(opens('электрон', 'электрически'))
        self.assertFalse(opens('электрически', 'электрон'))

    def test_reported_mississippi_sense_splits(self):
        self.assertFalse(opens('широта', 'обширной'))
        self.assertTrue(opens('обширность', 'обширный'))
        self.assertFalse(opens('конец', 'исконный'))
        self.assertTrue(opens('искони', 'исконный'))
        self.assertFalse(opens('включать', 'исключительно'))
        self.assertTrue(opens('исключать', 'исключение'))
        self.assertTrue(opens('исключительный', 'исключительно'))

    def test_fine_sense_splits_for_shir_kon_klyuch_chast(self):
        self.assertTrue(opens('широкий', 'расширение'))
        self.assertTrue(opens('широта', 'широкий'))
        self.assertTrue(opens('широта', 'широтный'))
        self.assertFalse(opens('широкий', 'широтный'))
        self.assertFalse(opens('ширина', 'ширинка'))

        self.assertTrue(opens('конец', 'окончание'))
        self.assertFalse(opens('конец', 'конечно'))
        self.assertFalse(opens('конец', 'досконально'))
        self.assertFalse(opens('конец', 'наконечник'))
        self.assertFalse(opens('конец', 'испокон'))
        self.assertTrue(opens('конец', 'конечность'))
        self.assertTrue(opens('кончина', 'скончаться'))

        self.assertFalse(opens('ключ', 'клюшка'))
        self.assertFalse(opens('ключ', 'уключина'))
        self.assertFalse(opens('включать', 'исключительный'))
        for include in ('включать', 'включаться', 'включение', 'включить', 'включиться'):
            for exclude in ('исключать', 'исключаться', 'исключение', 'исключить'):
                self.assertFalse(opens(include, exclude), (include, exclude))
                self.assertFalse(opens(exclude, include), (exclude, include))
        self.assertTrue(opens('исключение', 'исключительный'))
        self.assertTrue(opens('исключение', 'исключить'))

        self.assertTrue(opens('часть', 'частный'))
        self.assertTrue(opens('частник', 'частный'))
        self.assertFalse(opens('часть', 'частник'))
        self.assertFalse(opens('участие', 'участливый'))
        self.assertFalse(opens('участие', 'причащение'))
        self.assertTrue(opens('причастие', 'причащение'))

    def test_naibolee_joins_comparative_more(self):
        self.assertTrue(opens('более', 'наиболее'))
        self.assertTrue(opens('наиболее', 'более'))

    def test_neschastye_joins_luck_family(self):
        self.assertTrue(opens('счастье', 'несчастье'))
        self.assertTrue(opens('несчастный', 'несчастье'))

    def test_electron_and_electronics_are_distinct_clusters(self):
        from games.censorly.lexical.rootbank import structures_of

        self.assertTrue(_has_sense_structure('электрон', 'sense:electron_particle'))
        self.assertTrue(_has_sense_structure('электронный', 'sense:electronics'))
        self.assertTrue(_has_sense_structure('электроника', 'sense:electronics'))
        self.assertFalse(opens('электрон', 'электронный'))
        self.assertFalse(opens('электрон', 'электроника'))
        self.assertTrue(opens('электронный', 'электроника'))

    def test_sense_is_subclass_of_root_not_a_standalone_identity(self):
        from games.censorly.lexical.rootbank import (
            root_components, sense_components, structures_of,
        )

        application = structures_of('применение')
        attachment = structures_of('приложение')
        self.assertTrue(application and attachment)
        self.assertFalse(opens('применение', 'приложение'))
        self.assertFalse(opens('приложение', 'применение'))
        self.assertEqual(root_components(application), (('fam:мен¹',),))
        self.assertEqual(
            root_components(attachment),
            (('fam:лаг|лег²|леж|леч²|лог|лож¹',),),
        )
        self.assertEqual(
            sense_components(application), (('sense:review_application',),),
        )
        self.assertEqual(
            sense_components(attachment), (('sense:review_attachment',),),
        )

    def test_semantic_structures_always_have_a_root_component(self):
        from games.censorly.lexical.rootbank import (
            families_of, structures_of, tikhonov_roots_of,
        )
        from games.censorly.lexical.semantic_splits import CLUSTERS

        for _name, lemmas in CLUSTERS:
            for lemma in lemmas:
                if not families_of(lemma) and not tikhonov_roots_of(lemma):
                    self.assertTrue(structures_of(lemma), lemma)
                for structure in structures_of(lemma):
                    self.assertTrue(
                        any(not part.startswith('sense:') for part in structure),
                        lemma,
                    )

    def test_audit_confirmed_semantic_root_assignments_restore_only_clear_cases(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        expected = {
            'архитектура': 'root:architecture',
            'вспомнить': 'fam:мин¹|мн|мя²',
            'гладиатор': 'root:gladiator',
            'горестный': 'fam:гар|гор²',
            'живьем': 'fam:жи',
            'житейский': 'fam:жи',
            'житийный': 'fam:жи',
            'контрабанда': 'root:contraband',
            'концерт': 'root:concert',
            'неблизкий': 'fam:ближ|близ',
            'недорогой': 'fam:дорог¹|дорож¹|драж²',
            'операбельный': 'root:operation',
            'патриотизм': 'root:patriot',
            'приборный': 'fam:бер|бир|бор¹|бр¹',
            'революция': 'root:revolution',
            'рабыня': 'fam:раб',
            'снятый': 'fam:ем|им¹|йм|ним|ня|ым²|я¹|∅',
            'ткацкий': 'fam:тк|ток²|точ²|тык|тыч',
            'ужаленный': 'fam:жал²',
            'химизатор': 'root:chemistry',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma)[0][0], root)
                self.assertEqual(
                    assignment_of(lemma)['source'], 'SEMANTIC_ROOT_ASSIGNMENT',
                )

        # Multi-component or ambiguous Tikhonov analyses remain unresolved;
        # the audit must not turn a component resemblance into a root.
        for lemma in ('автомобиль', 'духовенство'):
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma), ())

    def test_numeric_forms_share_numeral_roots(self):
        from games.censorly.lexical.rootbank import structures_of

        for digit, word in (
            ('0', 'ноль'), ('1', 'один'), ('2', 'два'), ('3', 'три'),
            ('4', 'четыре'), ('5', 'пять'), ('6', 'шесть'), ('7', 'семь'),
            ('8', 'восемь'), ('9', 'девять'), ('10', 'десять'),
        ):
            with self.subTest(digit=digit):
                self.assertEqual(structures_of(digit), structures_of(word))
                self.assertTrue(opens(digit, word))
                self.assertTrue(opens(word, digit))
        self.assertFalse(opens('1', 'однажды'))
        self.assertFalse(opens('однажды', '1'))
        self.assertFalse(opens('1', 'заодно'))
        self.assertFalse(opens('заодно', '1'))

    def test_one_family_is_split_into_small_modern_senses(self):
        from games.censorly.lexical.rootbank import structures_of

        for lemma, sense in (
            ('один', 'sense:one'), ('одинарный', 'sense:one'),
            ('однажды', 'sense:once'), ('одиножды', 'sense:once'),
            ('заодно', 'sense:together_with'), ('одинешенек', 'sense:alone'),
            ('одинокий', 'sense:alone'), ('одинаковый', 'sense:same'),
            ('однако', 'sense:however'),
        ):
            self.assertTrue(_has_sense_structure(lemma, sense))
        for related in ('однажды', 'одиножды', 'заодно', 'одинокий', 'одинаковый', 'однако'):
            with self.subTest(related=related):
                self.assertFalse(opens('один', related))

    def test_predmet_is_not_the_sweep_root(self):
        from games.censorly.lexical.rootbank import structures_of

        self.assertTrue(_has_sense_structure('предмет', 'sense:object_item'))
        self.assertTrue(_has_sense_structure('метнуть', 'sense:sweep'))
        self.assertFalse(opens('предмет', 'метнул'))
        self.assertFalse(opens('метнул', 'предмет'))
        self.assertTrue(opens('предмет', 'предметный'))
        self.assertTrue(opens('метать', 'метнуть'))

    def test_sweep_family_has_clear_lexicalized_splits(self):
        from games.censorly.lexical.rootbank import structures_of

        clusters = {
            'сметана': 'sense:dairy',
            'заметка': 'sense:note',
            'разметка': 'sense:marking',
            'опрометчивый': 'sense:reckless',
        }
        for word, root in clusters.items():
            with self.subTest(word=word):
                self.assertTrue(_has_sense_structure(word, root))
                self.assertFalse(opens(word, 'метнул'))
                self.assertFalse(opens('метнул', word))

    def test_sweep_family_remaining_lexicalized_clusters_are_separate(self):
        from games.censorly.lexical.rootbank import structures_of

        clusters = {
            'заметка': 'sense:note',
            'приметка': 'sense:note',
            'наметка': 'sense:sewing_baste',
            'сметка': 'sense:sewing_baste',
            'обметка': 'sense:sewing_baste',
            'намет': 'sense:tent_stack',
            'подметка': 'sense:sole',
            'подметный': 'sense:anonymous_note',
            'помет': 'sense:dung_mark',
            'перемет': 'sense:fishing_net',
            'переметчивый': 'sense:fickle',
        }
        for word, root in clusters.items():
            with self.subTest(word=word):
                self.assertTrue(_has_sense_structure(word, root))
                self.assertFalse(opens(word, 'метнул'))
                self.assertFalse(opens('метнул', word))

    def test_one_word_cardinal_numerals_share_digit_roots(self):
        from games.censorly.lexical.rootbank import structures_of

        for digit, word in (
            ('11', 'одиннадцать'), ('12', 'двенадцать'),
            ('13', 'тринадцать'), ('14', 'четырнадцать'),
            ('15', 'пятнадцать'), ('16', 'шестнадцать'),
            ('17', 'семнадцать'), ('18', 'восемнадцать'),
            ('19', 'девятнадцать'), ('20', 'двадцать'),
            ('30', 'тридцать'), ('40', 'сорок'), ('50', 'пятьдесят'),
            ('60', 'шестьдесят'), ('70', 'семьдесят'),
            ('80', 'восемьдесят'), ('90', 'девяносто'),
            ('100', 'сто'), ('200', 'двести'), ('300', 'триста'),
            ('400', 'четыреста'), ('500', 'пятьсот'),
            ('600', 'шестьсот'), ('700', 'семьсот'),
            ('800', 'восемьсот'), ('900', 'девятьсот'),
            ('1000', 'тысяча'),
        ):
            with self.subTest(digit=digit):
                self.assertTrue(structures_of(word))
                self.assertEqual(structures_of(digit), structures_of(word))
                self.assertTrue(opens(digit, word))
                self.assertTrue(opens(word, digit))

    def test_domoy_does_not_use_spurious_domyit_reading(self):
        self.assertEqual(
            {(item.lemma, item.pos) for item in readings_of('домой')},
            {('домой', 'ADVB')},
        )
        self.assertFalse(opens('мыли', 'домой'))
        self.assertFalse(opens('домой', 'мыли'))

    def test_nem_pronoun_requires_preposition_in_article_context(self):
        from games.censorly.lexical.match import _GuessPack, _build_article_analysis, _relation
        from games.censorly.tokenize import tokenize_text

        for phrase in ('нем', 'нем.'):
            with self.subTest(phrase=phrase):
                analysis = _build_article_analysis(
                    tokenize_text(phrase), include_pronouns=False, cache_index=False,
                )
                target = analysis['index'].packs[0]
                self.assertNotIn((2303, 'он'), target.lexemes)
                self.assertNotIn((2306, 'оно'), target.lexemes)
                self.assertEqual(
                    _relation(
                        _GuessPack('него', include_pronouns=False), target,
                    ),
                    '',
                )

        analysis = _build_article_analysis(
            tokenize_text('о нем'), include_pronouns=False, cache_index=False,
        )
        target = analysis['index'].packs[-1]
        self.assertIn((2303, 'он'), target.lexemes)
        self.assertEqual(
            _relation(_GuessPack('него', include_pronouns=False), target),
            'lexeme',
        )

    def test_nem_pronoun_recognizes_preposition_before_modifier(self):
        from games.censorly.lexical.russian.reading_overrides import NEM_PRONOUN_CONTEXT
        from games.censorly.tokenize import tokenize_text

        tokens = tokenize_text('Говорили о самом нем.')
        nem = next(token for token in tokens if token.get('surface') == 'нем')
        self.assertEqual(nem['lemma'], NEM_PRONOUN_CONTEXT)

    def test_article_candidate_tools_use_current_article_index_api(self):
        from games.censorly.lexical.tools.article_candidates import find_open_pairs
        from games.censorly.lexical.tools.near_miss_candidates import find_near_misses
        from games.censorly.tokenize import build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Вода',
            body_text='Вода была холодной, водный поток быстро исчез.',
        )
        pairs, surface_count, _stats = find_open_pairs(
            payload, include_open=True, root_only=False,
        )
        near_misses, near_surface_count, _lemma_count = find_near_misses(payload)

        self.assertGreater(surface_count, 0)
        self.assertGreater(near_surface_count, 0)
        self.assertIsInstance(pairs, list)
        self.assertIsInstance(near_misses, list)

    def test_article_candidate_index_keeps_same_surface_readings_distinct(self):
        from games.censorly.lexical.core import fold
        from games.censorly.lexical.match import _ArticleIndex, _GuessPack, _relation
        from games.censorly.lexical.russian.reading_overrides import (
            NEM_GERMAN_CONTEXT,
            NEM_PRONOUN_CONTEXT,
        )
        from games.censorly.lexical.tools.article_candidates import _article_surfaces
        from games.censorly.tokenize import build_puzzle_payload

        payload = build_puzzle_payload(wiki_title='Контекст', body_text='Нем. О нем.')
        rows, _lemmas = _article_surfaces(payload)
        entries = tuple(sorted(rows))
        targets = _ArticleIndex(entries, include_pronouns=True)
        by_entry = {
            entry: pack for entry, pack in zip(entries, targets.packs)
        }
        guess = _GuessPack('нем', include_pronouns=True)

        self.assertIn((fold('нем'), NEM_GERMAN_CONTEXT), entries)
        self.assertIn((fold('нем'), NEM_PRONOUN_CONTEXT), entries)
        self.assertEqual(
            _relation(guess, by_entry[(fold('нем'), NEM_GERMAN_CONTEXT)]),
            'exact',
        )
        self.assertEqual(
            _relation(guess, by_entry[(fold('нем'), NEM_PRONOUN_CONTEXT)]),
            '',
        )

    def test_contextual_readings_are_stored_per_article_occurrence(self):
        from games.censorly.lexical.match import matching_lemmas
        from games.censorly.lexical.russian.reading_overrides import (
            CITY_ABBR_CONTEXT,
            NEM_GERMAN_CONTEXT,
            NEM_PRONOUN_CONTEXT,
            N_ERA_CONTEXT,
            PAGE_ABBR_CONTEXT,
            PO_SOFTWARE_CONTEXT,
            STREET_ABBR_CONTEXT,
            YEAR_ABBR_CONTEXT,
        )

        payload = build_puzzle_payload(
            wiki_title='Контекст чтения',
            body_text=(
                'ПО и по. Нем. язык не похож на воду. О нем. '
                'Наша н. э. началась позже. 5 г. назад в г. Москве '
                'прочитали на стр. 15 и ул. Пушкина. '
                'Вода стекла с крыши, разбитые стекла лежали рядом. 5 лет.'
            ),
        )
        tokens = payload['body_tokens']
        by_surface = {}
        for token in tokens:
            if token.get('kind') == 'content':
                by_surface.setdefault(token['surface'], []).append(token)

        self.assertEqual(by_surface['ПО'][0]['lemma'], PO_SOFTWARE_CONTEXT)
        self.assertFalse(by_surface['ПО'][0]['initially_open'])
        self.assertTrue(any(t['kind'] == 'stop' and t['surface'] == 'по' for t in tokens))
        self.assertEqual(by_surface['Нем'][0]['lemma'], NEM_GERMAN_CONTEXT)
        self.assertFalse(by_surface['Нем'][0]['initially_open'])
        self.assertEqual(by_surface['нем'][0]['lemma'], NEM_PRONOUN_CONTEXT)
        self.assertEqual(by_surface['нем'][0]['context_reading'], NEM_PRONOUN_CONTEXT)
        self.assertEqual(by_surface['н'][0]['lemma'], N_ERA_CONTEXT)
        self.assertEqual(by_surface['г'][0]['lemma'], YEAR_ABBR_CONTEXT)
        self.assertEqual(by_surface['г'][1]['lemma'], CITY_ABBR_CONTEXT)
        self.assertEqual(by_surface['стр'][0]['lemma'], PAGE_ABBR_CONTEXT)
        self.assertEqual(by_surface['ул'][0]['lemma'], STREET_ABBR_CONTEXT)
        self.assertEqual(by_surface['стекла'][0]['lemma'], 'стечь')
        self.assertEqual(by_surface['стекла'][1]['lemma'], 'стекло')
        self.assertEqual(by_surface['лет'][0]['lemma'], 'год')

        article_tokens = payload['title_tokens'] + tokens
        self.assertIn(PO_SOFTWARE_CONTEXT, matching_lemmas(article_tokens, 'по'))
        self.assertNotIn(PO_SOFTWARE_CONTEXT, matching_lemmas(article_tokens, 'программное'))
        self.assertEqual(
            matching_lemmas(article_tokens, 'нем'),
            {NEM_GERMAN_CONTEXT},
        )
        self.assertIn(NEM_GERMAN_CONTEXT, matching_lemmas(article_tokens, 'немецкий'))
        self.assertNotIn(NEM_PRONOUN_CONTEXT, matching_lemmas(article_tokens, 'немецкий'))
        self.assertIn(NEM_PRONOUN_CONTEXT, matching_lemmas(article_tokens, 'него'))
        self.assertNotIn(NEM_GERMAN_CONTEXT, matching_lemmas(article_tokens, 'него'))
        self.assertIn(N_ERA_CONTEXT, matching_lemmas(article_tokens, 'наша'))
        self.assertIn(YEAR_ABBR_CONTEXT, matching_lemmas(article_tokens, 'год'))
        self.assertIn(CITY_ABBR_CONTEXT, matching_lemmas(article_tokens, 'город'))
        self.assertIn(PAGE_ABBR_CONTEXT, matching_lemmas(article_tokens, 'страница'))
        self.assertIn(STREET_ABBR_CONTEXT, matching_lemmas(article_tokens, 'улица'))
        self.assertIn('стечь', matching_lemmas(article_tokens, 'течение'))
        self.assertNotIn('стекло', matching_lemmas(article_tokens, 'течение'))

    def test_contextual_token_readings_rebuild_from_old_article_snapshot(self):
        from games.censorly import CENSORLY_SPLITTER_VERSION
        from games.censorly.lexical.russian.reading_overrides import PO_SOFTWARE_CONTEXT
        from games.censorly.tokenize import resolve_puzzle_payload

        old = build_puzzle_payload(wiki_title='ПО', body_text='ПО')
        old['splitter_version'] = CENSORLY_SPLITTER_VERSION - 1
        old['body_tokens'][0].update({'kind': 'stop', 'lemma': 'по'})

        refreshed = resolve_puzzle_payload(old)

        self.assertEqual(refreshed['splitter_version'], CENSORLY_SPLITTER_VERSION)
        self.assertEqual(refreshed['body_tokens'][0]['kind'], 'content')
        self.assertEqual(refreshed['body_tokens'][0]['lemma'], PO_SOFTWARE_CONTEXT)

    def test_n_in_n_era_context_uses_our_lemma_only_there(self):
        from games.censorly.lexical.match import (
            _GuessPack, _build_article_analysis, _relation,
        )
        from games.censorly.tokenize import tokenize_text

        analysis = _build_article_analysis(
            tokenize_text('Наша н. э.'),
            include_pronouns=False,
            cache_index=False,
        )
        target = next(
            pack for surface, pack in zip(
                analysis['index'].surfaces, analysis['index'].packs,
            ) if surface == 'н'
        )
        self.assertIn((643, 'наш'), target.lexemes)
        self.assertTrue(target.roots)
        self.assertEqual(
            _relation(_GuessPack('Наша', include_pronouns=False), target),
            'lexeme',
        )

        standalone = _build_article_analysis(
            tokenize_text('н'), include_pronouns=False, cache_index=False,
        )
        target = standalone['index'].packs[0]
        self.assertNotIn((643, 'наш'), target.lexemes)
        self.assertEqual(
            _relation(_GuessPack('Наша', include_pronouns=False), target),
            '',
        )

    def test_valid_adverb_verb_homographs_remain_open(self):
        for adverb, verb in (
            ('стоя', 'стоить'),
            ('стоя', 'стоять'),
            ('сидя', 'сидеть'),
        ):
            with self.subTest(adverb=adverb, verb=verb):
                self.assertTrue(opens(adverb, verb))
                self.assertTrue(opens(verb, adverb))

    def test_almost_and_in_vain_are_not_honor_or_seeing_roots(self):
        for left, right in (('почти', 'почтить'), ('зря', 'зреть')):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

    def test_invalid_um_as_umeret_reading_does_not_open_mind(self):
        readings = readings_of('ум')
        self.assertEqual(
            {(item.lemma, item.pos) for item in readings},
            {('ум', 'NOUN')},
        )
        self.assertEqual(explain('смерть', 'ум'), '')
        self.assertEqual(explain('смерть', 'умом'), '')
        self.assertNotIn(
            'ум',
            matching_lemmas(
                [{'kind': 'content', 'surface': 'ум', 'lemma': 'ум'}],
                'смерть',
            ),
        )
        # Excluding the impossible surface reading must not disconnect the
        # actual verb or its valid inflected forms from the death root.
        self.assertEqual(explain('смерть', 'умереть'), 'root')
        self.assertEqual(explain('смерть', 'умер'), 'root')

    def test_required_matches(self):
        for guess, target, reason in TRUE_CASES:
            self.assertTrue(opens(guess, target), f'{guess}/{target} stayed closed')
            self.assertEqual(explain(guess, target), reason, f'{guess}/{target}')
            self.assertTrue(opens(target, guess), f'{target}/{guess}')

    @override_settings(CENSORLY_LEXICAL_RESOLVER=True)
    def test_title_related_words_are_protected_and_marked_weak(self):
        from games.censorly.redact import build_public_view, title_opening_lemmas

        payload = build_puzzle_payload(wiki_title='Кот', body_text='Котик спит.')
        body_word = next(
            tok for tok in payload['body_tokens']
            if tok.get('kind') == 'content' and tok.get('lemma')
        )

        self.assertIn(body_word['lemma'], title_opening_lemmas(payload))
        view = build_public_view(payload, revealed_lemmas=(), won=False)
        related = next(
            tok for tok in view['body_tokens'] if tok.get('id') == body_word['id']
        )
        self.assertTrue(related.get('title_related'))
        self.assertFalse(related.get('title_lemma'))

    @override_settings(CENSORLY_LEXICAL_RESOLVER=False)
    def test_related_title_protection_follows_matcher_flag(self):
        from games.censorly.redact import title_opening_lemmas

        payload = build_puzzle_payload(wiki_title='Кот', body_text='Котик спит.')
        body_lemma = next(
            tok['lemma'] for tok in payload['body_tokens']
            if tok.get('kind') == 'content' and tok.get('lemma')
        )
        self.assertNotIn(body_lemma, title_opening_lemmas(payload))

    @override_settings(
        CENSORLY_LEXICAL_RESOLVER=True,
        CENSORLY_OPEN_PRONOUNS=True,
    )
    def test_initially_open_title_word_keeps_strong_lock_metadata(self):
        from games.censorly.redact import build_public_view

        payload = build_puzzle_payload(wiki_title='Он', body_text='Он ждёт.')
        body_word = next(
            tok for tok in payload['body_tokens']
            if tok.get('kind') == 'content' and tok.get('lemma')
        )
        view = build_public_view(payload, revealed_lemmas=(), won=False)
        opened = next(
            tok for tok in view['body_tokens'] if tok.get('id') == body_word['id']
        )

        self.assertTrue(opened.get('revealed'))
        self.assertTrue(opened.get('title_lemma'))

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
        self.assertTrue(initially_open('он'))
        self.assertFalse(initially_open('пчела'))
        self.assertFalse(initially_open('равно'))

    def test_interjections_are_not_initially_open(self):
        for interjection in (
            'Эврика', 'ура', 'ой', 'ах', 'эй', 'увы', 'ого', 'фу',
            'алло', 'бис', 'аминь',
        ):
            with self.subTest(interjection=interjection):
                self.assertFalse(initially_open(interjection))

    def test_short_particle_di_is_not_initially_open(self):
        self.assertFalse(initially_open('Ди'))
        self.assertFalse(initially_open('ди'))

    def test_al_is_not_initially_open(self):
        self.assertFalse(initially_open('АЛЬ'))
        self.assertFalse(initially_open('аль'))

    def test_ibn_is_not_initially_open(self):
        for surface in ('ибн', 'Ибн', 'ИБН'):
            with self.subTest(surface=surface):
                self.assertFalse(initially_open(surface))

    def test_selected_service_homographs_are_not_initially_open(self):
        for word in (
            'фон', 'ван', 'дер', 'значит', 'короче', 'максимум',
            'бин', 'дель', 'ле', 'эль', 'ин', 'герне', 'мол', 'ка', 'ок', 'окей',
        ):
            for surface in (word, word.title(), word.upper()):
                with self.subTest(surface=surface):
                    self.assertFalse(initially_open(surface))

    def test_pronouns_start_open_across_inflectional_forms(self):
        for pronoun in (
            'я', 'меня', 'мне', 'мной', 'мною',
            'ты', 'тебя', 'тебе', 'тобой',
            'мы', 'нас', 'нам', 'нами', 'вы', 'вас', 'вам', 'вами',
            'он', 'его', 'ему', 'него', 'нему', 'ним',
            'она', 'её', 'ее', 'ей', 'ней', 'неё',
            'оно', 'они', 'их', 'им', 'ними', 'себя', 'себе', 'собой',
            'кто', 'никто', 'ничто', 'некто', 'нечто', 'некого',
            'нечего', 'никого', 'никому', 'ничему', 'кое-кто', 'кое-что',
        ):
            self.assertTrue(initially_open(pronoun), pronoun)

    def test_pronoun_adjectives_start_open(self):
        for pronoun in (
            'этот', 'эта', 'этого', 'этому', 'тот', 'та', 'того',
            'такой', 'такая', 'который', 'которого', 'какой', 'какая',
            'чей', 'чья', 'сам', 'сама', 'весь', 'вся', 'каждый',
            'любой', 'мой', 'моя', 'твой', 'наш', 'ваш', 'свой',
            'никакой', 'ничей', 'некоторый', 'один', 'оба', 'сколько',
            'несколько', 'какой-нибудь', 'чей-либо', 'таков', 'каков',
        ):
            self.assertTrue(initially_open(pronoun), pronoun)
        # Weak/ambiguous readings without an explicit initial-open override
        # stay closed.
        self.assertFalse(initially_open('пчела'))

    def test_selected_ambiguous_pronouns_start_open_without_opening_german(self):
        for surface in ('это', 'ничего', 'нем', 'нём'):
            with self.subTest(surface=surface):
                self.assertTrue(initially_open(surface))
        self.assertFalse(initially_open('немецкий'))

    def test_initial_pronoun_flag_is_enabled_by_default(self):
        from games.censorly.flags import open_pronouns_enabled

        self.assertTrue(open_pronouns_enabled())

    def test_single_digit_numerals_and_their_forms_start_open(self):
        for surface in (
            '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
            'ноль', 'одного', 'двумя', 'третьего', 'второго',
        ):
            with self.subTest(surface=surface):
                self.assertTrue(initially_open(surface))

    def test_multi_digit_numerals_and_troya_do_not_start_open(self):
        for surface in ('10', '11', 'десять', 'одиннадцать', 'десятый', 'троя'):
            with self.subTest(surface=surface):
                self.assertFalse(initially_open(surface))

    def test_hyphenated_pronouns_open_each_token_part(self):
        for compound in (
            'кто-то', 'кто-либо', 'кто-нибудь', 'кое-кто', 'кое-что',
            'какой-нибудь', 'какой-либо', 'чей-нибудь', 'чей-либо',
        ):
            words = [
                token for token in tokenize_text(compound)
                if token['kind'] not in {'space', 'punct'}
            ]
            self.assertTrue(words, compound)
            for token in words:
                opened = token['kind'] == 'stop' or initially_open(token['surface'])
                self.assertTrue(opened, f"{compound}: {token['surface']}")

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
        self.assertTrue(initially_open('один'))
        self.assertFalse(opens('один', 'первый'))
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
        self.assertTrue(opens('20', 'двадцать'))
        self.assertFalse(opens('I', 'II'))

    def test_split_subfamilies_are_disjoint(self):
        from games.censorly.lexical.semantic_splits import CLUSTERS
        from games.censorly.lexical.rootbank import structures_of
        by_name = {}
        seen = {}
        for name, lemmas in CLUSTERS:
            self.assertNotIn(name, by_name)
            by_name[name] = lemmas
            for lemma in lemmas:
                previous = seen.setdefault(lemma, [])
                if previous:
                    allowed = set(previous) | {name}
                    readings = {
                        part for item in structures_of(lemma)
                        for part in item if part.startswith('sense:')
                    }
                    self.assertTrue(
                        allowed.issubset(readings),
                        f'{lemma} in {previous} and {name} without separate readings',
                    )
                previous.append(name)
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

    def test_recent_multireadings_have_separate_root_structures(self):
        from games.censorly.lexical.rootbank import structures_of

        for lemma, senses in (
            ('помещение', {'sense:room', 'sense:placement'}),
            ('полка', {'sense:weeding', 'sense:shelf'}),
            ('член', {'sense:membership', 'sense:body_part', 'sense:formal_term'}),
        ):
            actual = {
                part for item in structures_of(lemma)
                for part in item if part.startswith('sense:')
            }
            self.assertEqual(actual, senses)
        self.assertFalse(opens('образ', 'образование'))
        self.assertFalse(opens('гора', 'горний'))
        self.assertTrue(opens('простой', 'упростить'))
        self.assertTrue(opens('простить', 'прощение'))

    def test_alternative_root_readings_are_not_a_compound(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        mary = structures_of('марь')
        self.assertEqual(
            {part for item in mary for part in item if part.startswith('sense:')},
            {'sense:haze', 'sense:plant'},
        )
        self.assertTrue(all(len(item) == 2 for item in mary))
        self.assertTrue(_has_sense_structure('маревый', 'sense:haze'))
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
        self.assertEqual(chrism['source'], 'SEMANTIC_ROOT_ASSIGNMENT')
        self.assertTrue(_has_sense_structure('миро', 'sense:chrism'))
        ambiguous = assignment_of('веризм')
        self.assertEqual(ambiguous['source'], 'UNRESOLVED')
        self.assertEqual(ambiguous['structures'], ())
        self.assertGreater(len(ambiguous['refused']), 1)
        self.assertFalse(opens('веризм', 'верить'))

    def test_compounds_keep_full_structures_only(self):
        from games.censorly.lexical.rootbank import (
            _ATOMIC_LEXICALIZED, _SENSE, assignment_of, provenance, structures_of,
        )

        self.assertIn(fold('красивенький'), _ATOMIC_LEXICALIZED)
        self.assertIn(fold('мелюзга'), _ATOMIC_LEXICALIZED)
        self.assertIn(fold('оживление'), _ATOMIC_LEXICALIZED)
        beauty = assignment_of('красивенький')
        self.assertEqual(beauty['source'], 'SEMANTIC_ROOT_ASSIGNMENT')
        self.assertTrue(_has_sense_structure('красивенький', 'sense:beauty'))
        self.assertTrue(opens('красивенький', 'красивый'))
        small = assignment_of('мелюзга')
        self.assertEqual(small['source'], 'SEMANTIC_ROOT_ASSIGNMENT')
        self.assertTrue(_has_sense_structure('мелюзга', 'sense:small'))
        animate = assignment_of('оживление')
        self.assertEqual(animate['source'], 'SEMANTIC_ROOT_ASSIGNMENT')
        self.assertTrue(_has_sense_structure('оживление', 'sense:animate'))
        self.assertTrue(opens('оживление', 'оживить'))
        for lemma, info in provenance().items():
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
            self.assertIn(fold('матка'), self._open_texts(mother))
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
    def test_troy_article_candidates_keep_names_and_separate_other_senses(self):
        from games.censorly.lexical.semantics import explain
        from games.censorly.lexical.rootbank import structures_of
        from games.censorly.tokenize import all_tokens, build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Проверка статьи о Трое',
            body_text=(
                'Троя была у моря. К западу пришел упадок. '
                'Правило отличалось от правления. Мора у моря. '
                'Вмещало местные поселения. Возведены перевод и вывод.'
            ),
        )
        tokens = all_tokens(payload)
        for guess, closed_lemma in (
            ('западу', 'упадок'),
            ('правило', 'правление'),
            ('мора', 'море'),
            ('вмещало', 'место'),
            ('вмещало', 'местный'),
            ('возведены', 'перевод'),
            ('возведены', 'вывод'),
        ):
            self.assertNotIn(closed_lemma, matching_lemmas(tokens, guess), guess)

        # This is a useful proper-name connection in this article, so keep it.
        self.assertEqual(explain('троя', 'троянец'), 'root')
        self.assertTrue(_has_sense_structure('троянец', 'sense:review_troy'))
        for lemma, sense in (
            ('вмещать', 'sense:containment'), ('место', 'sense:location'),
            ('возвести', 'sense:erection'), ('перевод', 'sense:translation'),
            ('вывод', 'sense:deduction_output'), ('приводить', 'sense:conduct'),
        ):
            self.assertTrue(_has_sense_structure(lemma, sense))

    def test_silver_article_root_collisions_are_split_or_context_filtered(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation
        from games.censorly.lexical.rootbank import structures_of
        from games.censorly.tokenize import all_tokens, build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Проверка кластеров серебра',
            body_text=(
                'Вместе место. Есть объем. Всей поверхности сей металл. '
                'Источник течение. Выключатель заключаются исключением. '
                'Использование полезных веществ. Вредности повреждения.'
            ),
        )
        tokens = all_tokens(payload)
        for guess, closed_lemma in (
            ('вместе', 'место'),
            ('есть', 'объем'),
            ('сей', 'весь'),
            ('течение', 'источник'),
            ('выключатель', 'заключаться'),
            ('выключатель', 'исключение'),
            ('включиться', 'исключение'),
            ('включать', 'исключение'),
            ('вредность', 'повреждение'),
        ):
            self.assertNotIn(closed_lemma, matching_lemmas(tokens, guess), guess)

        for lemma, sense in (
            ('вместе', 'sense:together'), ('место', 'sense:location'),
            ('источник', 'sense:source_origin'), ('течение', 'sense:flow'),
            ('выключатель', 'sense:switching'), ('заключаться', 'sense:conclusion'),
            ('использование', 'sense:review_utility'), ('полезный', 'sense:review_utility'),
            ('вредность', 'sense:review_harm'), ('повреждение', 'sense:review_physical_damage'),
        ):
            self.assertTrue(_has_sense_structure(lemma, sense))

        for guess, target in (
            ('вместе', 'совместный'),
            ('место', 'местный'),
            ('источник', 'исток'),
            ('включать', 'выключить'),
            ('исключать', 'исключение'),
            ('использование', 'использовать'),
            ('использование', 'полезный'),
            ('полезный', 'полезность'),
            ('вредность', 'вредный'),
            ('повреждение', 'повредить'),
        ):
            self.assertEqual(
                _relation(_GuessPack(guess), _Pack(target)),
                'root',
                f'{guess} / {target} should remain connected within its cluster',
            )

    def test_reported_article_words_follow_reviewed_sense_clusters(self):
        from games.censorly.lexical.rootbank import structures_of
        from games.censorly.tokenize import all_tokens, build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Проверка жалоб на матчинг',
            body_text=(
                'В основном занимался делом. Она использовалась. '
                'По выручке судили.'
            ),
        )
        tokens = all_tokens(payload)
        for guess, target_lemma in (
            ('основать', 'основное'),
            ('снять', 'заниматься'),
            ('руки', 'выручка'),
        ):
            with self.subTest(guess=guess, target=target_lemma):
                self.assertNotIn(target_lemma, matching_lemmas(tokens, guess))
        self.assertIn('использоваться', matching_lemmas(tokens, 'польза'))

        for left, right in (
            ('основа', 'основать'),
            ('основной', 'основный'),
            ('заниматься', 'занятие'),
            ('снять', 'снимать'),
            ('польза', 'использоваться'),
            ('рука', 'ручка'),
            ('выручка', 'недовыручка'),
            ('выручить', 'выручать'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

        self.assertNotEqual(structures_of('выручка'), structures_of('рука'))
        self.assertNotEqual(structures_of('основной'), structures_of('основать'))

    def test_reported_pour_settle_and_conduct_senses_are_split(self):
        for guess, target in (
            ('слияние', 'полей'),
            ('село', 'переселившим'),
            ('село', 'поселились'),
            ('проводимой', 'Повести'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(opens(guess, target))

        for left, right in (
            ('село', 'поселиться'),
            ('лить', 'слияние'),
            ('лить', 'литье'),
            ('лить', 'сливки'),
            ('лить', 'залив'),
            ('лить', 'прилив'),
            ('вести', 'провести'),
            ('вести', 'проводник'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

        for left, right in (
            ('село', 'сельский'),
            ('село', 'поселок'),
            ('поселиться', 'переселить'),
            ('поселиться', 'население'),
            ('лить', 'полить'),
            ('слияние', 'слиться'),
            ('слить', 'полить'),
            ('литье', 'слиток'),
            ('сливки', 'сливочный'),
            ('залив', 'пролив'),
            ('прилив', 'отлив'),
            ('провести', 'проводник'),
            ('проводить', 'провожать'),
            ('вести', 'повести'),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(explain(left, right), 'root')
                self.assertEqual(explain(right, left), 'root')

    def test_affected_families_keep_distinct_meanings_apart(self):
        closed_pairs = (
            ('основать', 'засновать'),
            ('заниматься', 'призанимать'),
            ('занятный', 'занятие'),
            ('заем', 'перенаем'),
            ('заем', 'заимка'),
            ('заимка', 'займище'),
            ('съемка', 'съемный'),
            ('рука', 'вручение'),
            ('вручную', 'вручение'),
            ('рука', 'поручительство'),
            ('рука', 'приручение'),
            ('рука', 'обручение'),
            ('поручик', 'поручительство'),
            ('выручить', 'выручка'),
        )
        for left, right in closed_pairs:
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

        connected_pairs = (
            ('сновать', 'засновать'),
            ('заниматься', 'подзаняться'),
            ('занятный', 'занятность'),
            ('нанять', 'перенаем'),
            ('заем', 'призанимать'),
            ('заем', 'перезанять'),
            ('снять', 'съемный'),
            ('съем', 'съемный'),
            ('вынуть', 'вынимать'),
            ('принятие', 'принять'),
            ('поем', 'пойма'),
            ('заимка', 'заимочный'),
            ('вручить', 'вручение'),
            ('рука', 'вручную'),
            ('поручить', 'поручительство'),
            ('приручить', 'приручение'),
            ('обручить', 'обручение'),
            ('поручик', 'подпоручик'),
            ('выручка', 'недовыручка'),
        )
        for left, right in connected_pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(explain(left, right), 'root')
                self.assertEqual(explain(right, left), 'root')

    def test_three_article_root_collisions_are_separated(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation
        from games.censorly.lexical.rootbank import structures_of
        from games.censorly.tokenize import all_tokens, build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Проверка корневых семейств',
            body_text='Будучи добыча. Стекла течение. Всего весьма.',
        )
        tokens = all_tokens(payload)
        for guess, closed_lemma in (
            ('будучи', 'добыча'),
            ('стекла', 'течение'),
            ('всего', 'весьма'),
        ):
            self.assertNotIn(closed_lemma, matching_lemmas(tokens, guess))

        for lemma, sense in (
            ('быть', 'sense:be'), ('добыча', 'sense:review_extraction'),
            ('стекло', 'sense:review_glass'), ('стечь', 'sense:flow'),
            ('весь', 'sense:review_whole'), ('весьма', 'sense:review_intensifier'),
        ):
            self.assertTrue(_has_sense_structure(lemma, sense))
        self.assertEqual(structures_of('всякий'), structures_of('всяческий'))

        for left, right in (
            ('добыча', 'добывать'),
            ('стекло', 'стеклянный'),
        ):
            self.assertTrue(
                _relation(_GuessPack(left), _Pack(right)),
                f'{left} / {right} should remain connected within its sense cluster',
            )

    def test_summer_is_separate_from_years(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation
        from games.censorly.lexical.rootbank import structures_of

        self.assertTrue(_has_sense_structure('лето', 'sense:review_summer'))
        self.assertTrue(_has_sense_structure('год', 'sense:review_year'))
        self.assertEqual(_relation(_GuessPack('ЛЕТО'), _Pack('ЛЕТ')), '')
        self.assertEqual(_relation(_GuessPack('ЛЕТ'), _Pack('ЛЕТО')), '')
        self.assertTrue(_relation(_GuessPack('лето'), _Pack('летний')))

    def test_pust_family_keeps_wasteland_and_trifle_labels(self):
        from games.censorly.lexical.semantic_splits import _classify_review_lemma

        family = 'пуст²|пущ¹'
        self.assertEqual(
            _classify_review_lemma(family, 'пустыня'),
            'sense:review_wasteland',
        )
        self.assertEqual(
            _classify_review_lemma(family, 'пустяк'),
            'sense:review_trifle',
        )

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

    def test_article_false_positive_roots_stay_apart_in_runtime_matcher(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation
        from games.censorly.tokenize import all_tokens, build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Проверка',
            body_text='Был была. Быть. Ш расширенный. Открытые покров. Образовать изображение.',
        )
        tokens = all_tokens(payload)
        opened_by_e = matching_lemmas(tokens, 'Е')
        self.assertNotIn('быть', opened_by_e)
        self.assertFalse(opens('Е', 'быть'))
        self.assertFalse(opens('быть', 'Е'))
        self.assertFalse(_relation(_GuessPack('Е'), _Pack('быть')))
        self.assertFalse(_relation(_GuessPack('быть'), _Pack('Е')))
        self.assertNotIn('расширить', matching_lemmas(tokens, 'Ш'))
        self.assertNotIn('покров', matching_lemmas(tokens, 'открытые'))
        self.assertNotIn('изображение', matching_lemmas(tokens, 'образовать'))
        self.assertNotIn('образовать', matching_lemmas(tokens, 'изображение'))

        for left, right in (
            ('открыть', 'открытие'),
            ('покрыть', 'покрытие'),
            ('изображать', 'изображение'),
            ('преобразовать', 'преобразование'),
        ):
            self.assertTrue(
                _relation(_GuessPack(left), _Pack(right)),
                f'{left} / {right} should remain connected within its sense cluster',
            )

    def test_evgeniy_does_not_open_zhenu(self):
        self.assertFalse(opens('евгений', 'жену'))
        self.assertFalse(opens('жену', 'евгений'))

    def test_merit_and_service_meanings_stay_apart(self):
        self.assertFalse(opens('заслуги', 'службу'))
        self.assertFalse(opens('службу', 'заслуги'))
        self.assertTrue(opens('заслуга', 'заслуженный'))
        self.assertTrue(opens('заслужить', 'заслуженный'))

    def test_divine_service_subfamily_is_narrow(self):
        self.assertTrue(opens('богослужение', 'богослужебный'))
        self.assertFalse(opens('богослужение', 'служение'))
        self.assertFalse(opens('священнослужитель', 'церковнослужитель'))

    def test_servility_subfamily_is_narrow(self):
        self.assertTrue(opens('прислуживать', 'прислужливый'))
        self.assertTrue(opens('услужливость', 'услужливый'))
        self.assertFalse(opens('прислужливый', 'служба'))
        self.assertFalse(opens('услужливый', 'услуга'))

    def test_show_and_meaning_families_are_narrow(self):
        self.assertFalse(opens('показатели', 'оказалась'))
        self.assertFalse(opens('оказалась', 'показатели'))
        self.assertFalse(opens('ознаменовало', 'назначить'))
        self.assertFalse(opens('назначить', 'ознаменовало'))
        self.assertTrue(opens('показатель', 'показательный'))
        self.assertTrue(opens('оказаться', 'оказать'))
        self.assertTrue(opens('ознаменовать', 'ознаменование'))
        self.assertTrue(opens('назначить', 'назначение'))

    def test_creation_and_building_families_are_narrow(self):
        self.assertFalse(opens('создатель', 'здание'))
        self.assertFalse(opens('здание', 'создатель'))
        self.assertTrue(opens('создатель', 'создание'))
        self.assertTrue(opens('здание', 'зданьице'))
        self.assertTrue(opens('созидатель', 'создание'))
        self.assertFalse(opens('зиждитель', 'создатель'))
        self.assertFalse(opens('зодчий', 'здание'))
        self.assertFalse(opens('назидание', 'создание'))

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

    def test_latest_careful_split_batch_stays_apart(self):
        pairs = (
            ('вередить', 'вредить'),
            ('здание', 'назидание'),
            ('создать', 'зодчий'),
            ('сукно', 'засучить'),
            ('добрый', 'удобрение'),
            ('удобрение', 'сдабривать'),
            ('страж', 'предостеречь'),
            ('предостеречь', 'подстерегать'),
            ('лесть', 'прелесть'),
        )
        for left, right in pairs:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')


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
        self.assertEqual(assignment_of('атом')['source'], 'REVIEWED_OPAQUE_ROOT')
        self.assertEqual(structures_of('атом'), (('tikh:атом',),))
        self.assertEqual(structures_of('атомный'), (('tikh:атом',),))
        self.assertTrue(opens('атом', 'атомным'))

        proza_family = ('проза', 'прозаик', 'прозаический', 'прозаичный', 'прозаизм')
        for lemma in proza_family:
            self.assertEqual(assignment_of(lemma)['source'], 'REVIEWED_OPAQUE_ROOT')
            self.assertEqual(structures_of(lemma), (('tikh:проз',),))
        for left in proza_family:
            for right in proza_family:
                if left != right:
                    self.assertTrue(opens(left, right), f'{left}/{right}')

    def test_reviewed_assignments_keep_semantic_splits_closed(self):
        for left, right in (
            ('страна', 'странный'),
            ('крупа', 'крупный'),
            ('свет', 'светский'),
            ('червь', 'червовый'),
            ('мара', 'маревый'),
            ('год', 'годный'),
            ('душа', 'душный'),
        ):
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')

    def test_root_sense_migration_keeps_confirmed_false_negatives_open(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        pairs = (
            ('предпочесть', 'предпочитать'),
            ('близкий', 'неблизкий'),
            ('плотнеть', 'уплотнение'),
            ('почет', 'почитание'),
            ('чертёжный', 'черчение'),
            ('связь', 'связать'),
            ('оставление', 'оставлять'),
            ('пихать', 'пихаться'),
            ('пустынник', 'пустыня'),
        )
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))
                self.assertTrue(structures_of(left))
                self.assertTrue(structures_of(right))
                self.assertNotEqual(assignment_of(left)['source'], 'UNRESOLVED')
                self.assertNotEqual(assignment_of(right)['source'], 'UNRESOLVED')

        # The assignments apply to the semantic families, not just the two
        # submitted surfaces.  Inflectional and ordinary derivatives remain
        # rooted as well.
        for left, right in (
            ('предпочтение', 'предпочтительный'),
            ('уплотнить', 'уплотненный'),
            ('почтить', 'чтить'),
            ('оставить', 'оставление'),
            ('пихнуть', 'пихание'),
            ('пустынный', 'пустынница'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))

        for left, right in (
            ('оставить', 'остальной'),
            ('остаться', 'остаток'),
            ('оставление', 'останки'),
            ('оставление', 'остаток'),
            ('оставлять', 'остаточный'),
            ('останки', 'остаток'),
            ('остальной', 'остаточный'),
            ('пустыня', 'пустошь'),
            ('пустыня', 'пустырь'),
            ('пустынник', 'пустырник'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))

        self.assertTrue(opens('остаток', 'остаточный'))

    def test_reviewed_root_alternants_and_semantic_boundaries(self):
        from games.censorly.lexical.rootbank import structures_of

        for left, right in (
            ('идти', 'ходить'),
            ('найти', 'находить'),
            ('перелететь', 'лететь'),
            ('перелетать', 'лететь'),
            ('показание', 'показания'),
            ('чертить', 'чертёж'),
            ('горе', 'пригорюниться'),
            ('сходственный', 'сходство'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

        self.assertFalse(opens('идти', 'найти'))
        self.assertFalse(opens('найти', 'идти'))

        for left, right in (
            ('применение', 'приложение'),
            ('показание', 'свидетельство'),
            ('убирать', 'чистить'),
            ('добывать', 'вынимать'),
            ('звонить', 'взывать'),
            ('нарушать', 'громить'),
            ('поддерживать', 'опираться'),
            ('страсть', 'пристрастие'),
            ('родина', 'отечество'),
            ('посылать', 'посол'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

        # The compound reading of «простой» remains complete and does not
        # leak into the unrelated forgiveness/standing families.
        self.assertTrue(opens('простой', 'упростить'))
        self.assertFalse(opens('простой', 'простить'))
        self.assertFalse(opens('простой', 'состоять'))

        # «ведение» is polysemous: the knowledge reading can match «ведать»,
        # while its administration reading remains separately represented.
        self.assertTrue(opens('ведать', 'ведение'))
        self.assertTrue(opens('честь', 'почет'))

    def test_single_root_unresolved_semantic_members_are_authorized(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        expected = {
            'двоешка': 'fam:дв',
            'солодовенный': 'fam:слад|слажд|сласт|слащ|солаж|солод|солож|солощ',
            'горняцкий': 'fam:гор¹',
            'отягощение': 'fam:тя|тяг|тяж|тяз',
            'обожествление': 'fam:бог|бож',
            'столешник': 'fam:сл²|стел|стил|стл|стол',
            'узорочье': 'fam:зар|зер|зир|зор|зр',
            'литейщик': 'fam:ле(j)|ли|ло(j)|ль²',
            'обобществление': 'fam:общ',
            'верховенство': 'fam:верх|верш¹',
            'трешка': 'fam:тр¹',
            'родненький': 'fam:род|рож²|рожд',
            'синюшка': 'fam:син',
            'мальчишечка': 'fam:мал¹',
            'чистюля': 'fam:чист|чищ',
            'трещоточный': 'fam:трес|треск¹|трещ',
            'твердыня': 'fam:тверд|тверж|твержд',
            'крышечный': 'fam:кров²|кры',
            'боевитость': 'fam:би(j)|бо(j)¹|бь',
            'праматерь': 'fam:мат¹|мач¹',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma)[0][0], root)
                self.assertEqual(
                    assignment_of(lemma)['source'], 'SEMANTIC_ROOT_ASSIGNMENT',
                )

    def test_prefixed_unresolved_semantic_members_restore_existing_root_families(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        expected = {
            'россказни': 'fam:каж¹|кажд¹|каз¹',
            'приказчичий': 'fam:каж¹|кажд¹|каз¹',
            'складчинный': 'fam:кла|клад|клаж|клас',
            'врастяжку': 'fam:тя|тяг|тяж|тяз',
            'враскачку': 'fam:кат¹|кач¹',
            'росплывь': 'fam:пла|плав|плов|плы',
            'роспуск': 'fam:пуск|пуст¹|пущ²',
            'роспуски': 'fam:пуск|пуст¹|пущ²',
            'росчисть': 'fam:чист|чищ',
            'росчерк': 'fam:черк',
            'роспись': 'fam:пис|пиш',
            'враструску': 'fam:трус³|трух²|труш',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma)[0][0], root)
                self.assertEqual(
                    assignment_of(lemma)['source'], 'SEMANTIC_ROOT_ASSIGNMENT',
                )

        for left, right in (
            ('россказни', 'рассказ'),
            ('приказчичий', 'приказчик'),
            ('складчинный', 'складчина'),
            ('врастяжку', 'растяжение'),
            ('враскачку', 'раскачиваться'),
            ('росплывь', 'сплывать'),
            ('роспуск', 'выпуск'),
            ('роспуски', 'выпуск'),
            ('росчисть', 'расчистить'),
            ('росчерк', 'прочерк'),
            ('роспись', 'писать'),
            ('враструску', 'раструсить'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

    def test_reviewed_unresolved_sense_boundaries(self):
        from games.censorly.lexical.rootbank import structures_of

        exclamation = (
            'fam:клик|клиц|клич', 'sense:review_exclamation',
        )
        for lemma in (
            'восклицание', 'восклицательный', 'восклицать', 'воскликнуть',
        ):
            with self.subTest(lemma=lemma):
                self.assertIn(exclamation, structures_of(lemma))

        self.assertIn(
            ('fam:клик|клиц|клич', 'sense:review_click'),
            structures_of('кликнуть'),
        )
        self.assertTrue(opens('восклицание', 'воскликнуть'))
        self.assertFalse(opens('воскликнуть', 'кликнуть'))

        self.assertIn(
            ('fam:мал¹', 'sense:review_small_creature'),
            structures_of('малявка'),
        )
        self.assertNotEqual(
            structures_of('малявка'), structures_of('маленький'),
        )

        self.assertIn(
            ('fam:пут²', 'sense:ticket'),
            structures_of('путевка'),
        )
        self.assertFalse(opens('путевка', 'путевой'))

        self.assertIn(
            ('fam:хаж|ход|хож|хожд', 'sense:review_occurrence'),
            structures_of('происходить'),
        )
        self.assertIn(
            ('fam:хаж|ход|хож|хожд', 'sense:review_occurrence'),
            structures_of('происходящий'),
        )
        self.assertIn(
            ('fam:хаж|ход|хож|хожд', 'sense:review_outcome_origin'),
            structures_of('происхождение'),
        )
        self.assertFalse(opens('происходить', 'происхождение'))

    def test_article_root_review_batch(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        for left, right in (
            ('беспомощный', 'мочь'),
            ('близкий', 'приблизительный'),
        ):
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')

        for root, left, right in (
            ('ассоци', 'ассоциация', 'ассоциировать'),
            ('благодар', 'благодарность', 'благодарный'),
        ):
            self.assertEqual(assignment_of(left)['source'], 'REVIEWED_OPAQUE_ROOT')
            self.assertEqual(structures_of(left), ((f'tikh:{root}',),))
            self.assertEqual(structures_of(right), ((f'tikh:{root}',),))
            self.assertTrue(opens(left, right), f'{left}/{right}')
            self.assertTrue(opens(right, left), f'{right}/{left}')

        self.assertTrue(opens('биограф', 'биография'))
        self.assertTrue(opens('биография', 'биографический'))
        self.assertTrue(opens('выписать', 'написать'))

    def test_article_play_family_links_and_semantic_splits(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation

        linked = (
            ('география', 'географическая'),
            ('романтизм', 'роман'),
            ('поэт', 'поэтическому'),
            ('кадет', 'кадетский'),
            ('написал', 'письме'),
            ('писать', 'писатель'),
        )
        for left, right in linked:
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

        split = (
            ('толика', 'столько'),
            ('данное', 'удалось'),
            ('удалось', 'данной'),
            ('удалось', 'раздав'),
            ('удалось', 'преданию'),
            ('убирает', 'приборы'),
            ('отец', 'Отечественной'),
            ('отца', 'вотчин'),
            ('конец', 'скончавшейся'),
            ('состоятельных', 'состоянии'),
            ('поездка', 'проезжей'),
            ('поездка', 'уезде'),
            ('послание', 'ссылаясь'),
            ('показания', 'оказало'),
            ('показания', 'показан'),
            ('разрыв', 'порывы'),
            ('старших', 'староста'),
        )
        for left, right in split:
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

        # «дорога» has a valid short-adjective homograph in pymorphy.  The
        # article path knows which lemma was selected, so certify the intended
        # noun-vs-adjective split with contextual packs rather than deleting a
        # valid existential reading globally.
        self.assertNotEqual(
            _relation(
                _GuessPack('дорогое'), _Pack('дорога', article_lemma='дорога')
            ),
            'root',
        )

    def test_article_context_filters_send_homographs(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation

        for surface, lemma in (
            ('вышел', 'выйти'),
            ('вышла', 'выйти'),
            ('вышли', 'выйти'),
            ('нашли', 'найти'),
        ):
            with self.subTest(surface=surface, lemma=lemma):
                self.assertNotEqual(
                    _relation(_GuessPack('послание'), _Pack(surface, article_lemma=lemma)),
                    'root',
                )

    def test_article_context_separates_stat_article_from_become(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation

        article = _Pack('статью', article_lemma='статья')
        self.assertEqual(_relation(_GuessPack('стал'), article), '')
        self.assertEqual(_relation(_GuessPack('стать'), article), '')
        self.assertEqual(_relation(_GuessPack('статья'), article), 'lexeme')

        self.assertTrue(opens('стать', 'становиться'))
        self.assertFalse(opens('стать', 'статья'))
        self.assertFalse(opens('стать', 'статный'))

    def test_operation_and_opera_families_are_separate(self):
        operation = (
            'операция', 'операционный', 'оперировать', 'оператор', 'оперативный',
        )
        opera = ('опера', 'оперный', 'оперетта', 'оперетка')
        for left in operation:
            for right in operation:
                with self.subTest(cluster='operation', left=left, right=right):
                    self.assertTrue(opens(left, right))
        for left in opera:
            for right in opera:
                with self.subTest(cluster='opera', left=left, right=right):
                    self.assertTrue(opens(left, right))
        for left in operation:
            for right in opera:
                with self.subTest(cluster='split', left=left, right=right):
                    self.assertFalse(opens(left, right))

        from games.censorly.lexical.match import matching_lemmas
        from games.censorly.tokenize import tokenize_text

        self.assertIn('операционный', matching_lemmas(
            tokenize_text('операционная'), 'операция',
        ))

    def test_program_and_programmatic_adjective_share_reviewed_family(self):
        self.assertTrue(opens('программа', 'программного'))
        self.assertTrue(opens('программой', 'программы'))

    def test_man_hour_and_carry_families_are_split_by_sense(self):
        self.assertFalse(opens('муж', 'мужчин'))
        self.assertTrue(opens('мужчина', 'мужской'))

        self.assertFalse(opens('перенесли', 'относиться'))
        self.assertTrue(opens('перенесли', 'перенести'))
        self.assertTrue(opens('относиться', 'отношение'))

        self.assertFalse(opens('час', 'часовне'))
        self.assertTrue(opens('часовня', 'часовенка'))
        self.assertFalse(opens('час', 'часовщик'))
        self.assertTrue(opens('часовщик', 'часовщичий'))

    def test_application_and_attachment_families_are_separate(self):
        for left, right in (
            ('применение', 'применять'),
            ('приложение', 'приложить'),
            ('приложение', 'приложений'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
        self.assertFalse(opens('применение', 'приложение'))
        self.assertFalse(opens('применение', 'приложений'))

    def test_send_family_has_narrow_modern_senses(self):
        for left, right in (
            ('послать', 'послание'),
            ('послание', 'посланник'),
            ('выслать', 'высылка'),
            ('наслать', 'насылать'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

        for left, right in (
            ('выслать', 'послание'),
            ('наслать', 'послание'),
            ('выслать', 'наслать'),
            ('прислать', 'отослать'),
            ('разослать', 'переслать'),
            ('дослать', 'заслать'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

    def test_citation_nouns_do_not_use_gerund_or_adjective_homographs(self):
        for left, right in (
            ('дорога', 'дорогой'),
            ('разрыв', 'разрыть'),
            ('порыв', 'порыть'),
            ('устав', 'устать'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

        # Genuine noun/noun alternatives remain available; this is not a
        # blanket "one citation reading wins" rule.
        self.assertTrue(opens('улей', 'улья'))
        self.assertTrue(opens('улья', 'улей'))

    def test_light_and_enlightenment_families_are_split(self):
        for right in (
            'просвет', 'просветить', 'просветление', 'просвещённый',
        ):
            with self.subTest(right=right):
                self.assertFalse(opens('свет', right))
                self.assertFalse(opens(right, 'свет'))

    def test_falling_and_west_families_are_separate(self):
        falling = ('упадок', 'упадёт', 'упадет', 'падать')
        west = ('запад', 'западник')
        for left in falling:
            for right in west:
                with self.subTest(left=left, right=right):
                    self.assertFalse(opens(left, right))
                    self.assertFalse(opens(right, left))
        self.assertTrue(opens('запад', 'западник'))
        self.assertTrue(opens('западник', 'запад'))

    def test_reviewed_chemistry_and_norm_subfamilies(self):
        from games.censorly.lexical.rootbank import assignment_of, structures_of

        for left, right in (
            ('норма', 'норматив'),
            ('антисептик', 'антисептический'),
            ('сульфид', 'сульфидный'),
            ('хлор', 'хлорид'),
            ('химически', 'химический'),
        ):
            self.assertTrue(opens(left, right), f'{left}/{right}')
            self.assertTrue(opens(right, left), f'{right}/{left}')

        self.assertEqual(assignment_of('норма')['source'], 'REVIEWED_OPAQUE_ROOT')
        self.assertEqual(structures_of('норма'), (('tikh:норм',),))

        for left, right in (
            ('сульфид', 'сульфат'),
            ('сульфид', 'сульфит'),
            ('сульфид', 'сульфирование'),
            ('сульфид', 'сульфированный'),
            ('сульфид', 'сульфидин'),
            ('хлор', 'хлорат'),
            ('хлор', 'хлорит'),
            ('хлор', 'хлороз'),
            ('культура', 'культурный'),
            ('датироваться', 'датировка'),
            ('эллинистический', 'эллинский'),
            ('археолог', 'археологический'),
            ('античность', 'античный'),
            ('троя', 'троянец'),
            ('троянец', 'троянский'),
        ):
            self.assertTrue(opens(left, right), f'{left}/{right}')
            self.assertTrue(opens(right, left), f'{right}/{left}')

        for left, right in (
            ('мать', 'матушка'),
            ('мать', 'материнский'),
            ('матка', 'маточный'),
            ('матка', 'маточник'),
        ):
            self.assertTrue(opens(left, right), f'{left}/{right}')
            self.assertTrue(opens(right, left), f'{right}/{left}')

        self.assertTrue(opens('мать', 'матка'))
        self.assertTrue(opens('матка', 'мать'))

        for left, right in (
            ('антисептик', 'септима'),
            ('хлор', 'хлоропласт'),
            ('химически', 'биохимия'),
        ):
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')

PRODUCTION_CONFIRMED_CENSORLY_CASES = (
    # Snapshot of the confirmed Censorly word reports from production.
    ('сказка', 'высказать', 'CLOSED'),
    ('имеются', 'занимался', 'CLOSED'),
    ('народ', 'рождественской', 'CLOSED'),
    ('был', 'Е', 'CLOSED'),
    ('понятие', 'внимания', 'CLOSED'),
    ('смерть', 'умом', 'CLOSED'),
    ('понятие', 'внимание', 'CLOSED'),
    ('женился', 'женщиной', 'CLOSED'),
    ('известный', 'Повести', 'CLOSED'),
    ('писатель', 'надпись', 'CLOSED'),
    ('главный', 'заглавием', 'CLOSED'),
    ('видеть', 'свидания', 'CLOSED'),
    ('известный', 'навещал', 'CLOSED'),
    ('видеть', 'свидетельствует', 'CLOSED'),
    ('проза', 'прозаик', 'OPEN'),
    ('область', 'Обладала', 'CLOSED'),
    ('советский', 'советоваться', 'CLOSED'),
    ('прослеживаться', 'впоследствии', 'CLOSED'),
    ('открытые', 'покров', 'CLOSED'),
    ('образовать', 'изображение', 'CLOSED'),
    ('были', 'Е', 'CLOSED'),
    ('География', 'Географическая', 'OPEN'),
    ('дорога', 'дорогое', 'CLOSED'),
    ('толика', 'столько', 'CLOSED'),
    ('другой', 'друга', 'CLOSED'),
    ('романтизм', 'роман', 'OPEN'),
    ('поэт', 'поэтическому', 'OPEN'),
    ('данное', 'удалось', 'CLOSED'),
    ('кадет', 'кадетский', 'OPEN'),
    ('Вторжение', 'торжествующая', 'CLOSED'),
    ('иногда', 'иных', 'CLOSED'),
    ('убирает', 'приборы', 'CLOSED'),
    ('удалось', 'данной', 'CLOSED'),
    ('отец', 'Отечественной', 'CLOSED'),
    ('любую', 'предпочитают', 'CLOSED'),
    ('конец', 'скончавшейся', 'CLOSED'),
    ('написал', 'письме', 'OPEN'),
    ('состоятельных', 'состоянии', 'CLOSED'),
    ('поездка', 'проезжей', 'CLOSED'),
    ('послание', 'вышел', 'CLOSED'),
    ('послание', 'вышли', 'CLOSED'),
    ('послание', 'вышла', 'CLOSED'),
    ('послание', 'ссылаясь', 'CLOSED'),
    ('послание', 'нашли', 'CLOSED'),
    ('показания', 'оказало', 'CLOSED'),
    ('показания', 'показан', 'CLOSED'),
    ('разрыв', 'порывы', 'CLOSED'),
    ('отца', 'вотчин', 'CLOSED'),
    ('старших', 'староста', 'CLOSED'),
    ('поездка', 'уезде', 'CLOSED'),
    ('удалось', 'раздав', 'CLOSED'),
    ('удалось', 'преданию', 'CLOSED'),
)


class NewConfirmedSingleTokenRelationTests(SimpleTestCase):
    def test_confirmed_production_reports(self):
        for guess, target, expected in PRODUCTION_CONFIRMED_CENSORLY_CASES:
            with self.subTest(guess=guess, target=target):
                actual = explain_pair(guess, target)['runtime_reason'] != 'CLOSED'
                self.assertEqual(actual, expected == 'OPEN')

    def test_confirmed_pair_reports_have_regressions(self):
        # Keep the user-reviewed report set visible in the executable
        # regression suite.  A typed «пара» prefers its citation-form
        # readings over the inflected homonym «пар».
        expected_open = (
            ('сенат', 'сенатор'),
            ('иволга', 'иволговый'),
            ('белка', 'белки'),
            ('белок', 'белки'),
            ('центральный', 'центр'),
            ('Наполеон', 'Наполеоновский'),
        )
        expected_closed = (
            ('мир', 'море'),
            ('пчела', 'пчеловод'),
            ('мёд', 'медоносный'),
            ('страна', 'странный'),
            ('крупа', 'крупный'),
            ('свет', 'светский'),
            ('червь', 'червовый'),
            ('мара', 'маревый'),
            ('год', 'годный'),
            ('душа', 'душный'),
            ('устав', 'уставать'),
            ('строить', 'три'),
        )
        for left, right in expected_open:
            self.assertTrue(opens(left, right), f'{left}/{right}')
            self.assertTrue(opens(right, left), f'{right}/{left}')
        for left, right in expected_closed:
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')
        self.assertFalse(opens('пара', 'испарять'))

    def test_confirmed_proper_pairs_are_bidirectional(self):
        from games.censorly.lexical.proper_names import PAIR_RELATIONS

        self.assertEqual(len(PAIR_RELATIONS), 572)
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

    def test_proper_pair_components_connect_adjectives_and_demonyms(self):
        from collections import defaultdict

        from games.censorly.lexical.proper_names import PAIR_RELATIONS
        from games.censorly.lexical.semantics import _proper_pair

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
                    self.assertTrue(_proper_pair(adjective, demonym))
                    self.assertTrue(_proper_pair(demonym, adjective))

    def test_new_pairs_are_not_accidentally_clustered(self):
        from games.censorly.lexical.proper_names import CLUSTERS, PAIR_RELATIONS

        clustered = {word for _name, words in CLUSTERS for word in words}
        legacy_cluster_bases = {
            'бразилия', 'беларусь', 'германия', 'канада', 'россия', 'украина',
        }
        for left, right in PAIR_RELATIONS:
            # пушкин is a legacy CLUSTERS member predating the explicit
            # eponym pair; its new derivative is tested for non-transitivity
            # separately in test_censorly_eponyms.py.
            if left in {'пушкин', 'российский', 'русь'} or left in legacy_cluster_bases:
                continue
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
