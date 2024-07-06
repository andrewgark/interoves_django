source ../virt3/bin/activate
sudo ntpdate ntp.ubuntu.com
python3 manage.py makemigrations games 
python3 manage.py migrate
python3 manage.py collectstatic --no-input 
python3 manage.py runserver
