# Use Python from venv (no activation needed)
PYTHON="../venv/interoves_django/bin/python3"
sudo ntpdate ntp.ubuntu.com
"$PYTHON" manage.py makemigrations games
"$PYTHON" manage.py migrate
"$PYTHON" manage.py runserver
