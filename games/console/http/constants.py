SUPPORT_CONSOLE_GROUP = 'Support Console'
# Each opens only its own section of the console, nothing else.
ADVENT_CONSOLE_GROUP = 'Advent Calendar'
CRYPTIC_CONSOLE_GROUP = 'Cryptics'
# Section id → (group, console URL, nav title).
LIMITED_CONSOLE_SECTIONS = (
    ('advent', ADVENT_CONSOLE_GROUP, '/support/advent/', 'Адвент-календарь'),
    ('cryptic', CRYPTIC_CONSOLE_GROUP, '/support/cryptic/', 'Криптики'),
)
