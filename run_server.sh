# Python: project venv at ../venv/interoves_django (relative to repo root)
PYTHON="../venv/interoves_django/bin/python3"
sudo ntpdate ntp.ubuntu.com
"$PYTHON" manage.py makemigrations games
"$PYTHON" manage.py migrate
"$PYTHON" manage.py runserver
