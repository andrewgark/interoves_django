"""
Django settings for interoves_django project.

Generated by 'django-admin startproject' using Django 2.1.1.

For more information on this file, see
https://docs.djangoproject.com/en/2.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.1/ref/settings/
"""

import os
import requests
import sys


IS_PROD = os.getenv('IS_PROD') == 'TRUE'

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_secret(secret):
    file = open(os.path.join(BASE_DIR, 'secrets', secret))
    result = file.read().strip()
    file.close()
    return result


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.1/howto/deployment/checklist/

SECRET_KEY = load_secret('django_secret_key.txt')

DEBUG = not IS_PROD or os.getenv('DEBUG_ON') == 'TRUE'

ALLOWED_HOSTS = [
    'interoves-django-env.eba-nbcqahns.eu-central-1.elasticbeanstalk.com',
    'interoves-django.eba-nbcqahns.eu-central-1.elasticbeanstalk.com',
    'interoves.eu-central-1.elasticbeanstalk.com',
    'interoves-django-env.eu-central-1.elasticbeanstalk.com',
    'ec2-35-158-115-233.eu-central-1.compute.amazonaws.com',
    '172.31.43.189',
    'interoves.ml',
    'www.interoves.ml',
    '127.0.0.1',
    'fat-owl-8.loca.lt'
]

def get_ec2_instance_ip():
    """
    Try to obtain the IP address of the current EC2 instance in AWS
    """
    try:
        ip = requests.get(
          'http://169.254.169.254/latest/meta-data/local-ipv4',
          timeout=0.01
        ).text
    except requests.exceptions.ConnectionError:
        return None
    return ip

AWS_LOCAL_IP = get_ec2_instance_ip()
ALLOWED_HOSTS.append(AWS_LOCAL_IP)

# Application definition

INSTALLED_APPS = [
    'games',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.vk',
    'games.telegram',

    'django_telegram_login',

    'health_check',
    'health_check.db',
    'health_check.cache',
    'health_check.storage',

    'storages',

    'corsheaders',

    'yet_another_django_profiler',

    'inlineedit',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',

    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    'yet_another_django_profiler.middleware.ProfilerMiddleware',
]

ROOT_URLCONF = 'interoves_django.urls'

TEMPLATE_DIR = os.path.join(BASE_DIR, "static", "templates")
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            TEMPLATE_DIR,
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'interoves_django.wsgi.application'


# Database
# https://docs.djangoproject.com/en/2.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}

if 'RDS_HOSTNAME' in os.environ:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.environ['RDS_DB_NAME'],
            'USER': os.environ['RDS_USERNAME'],
            'PASSWORD': os.environ['RDS_PASSWORD'],
            'HOST': os.environ['RDS_HOSTNAME'],
            'PORT': os.environ['RDS_PORT'],
        }
    }

# Password validation
# https://docs.djangoproject.com/en/2.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/2.1/topics/i18n/

LANGUAGE_CODE = 'ru-ru'

TIME_ZONE = 'Europe/Moscow'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.1/howto/static-files/

USE_S3 = os.getenv('USE_S3') == 'TRUE'

if USE_S3:
    # aws settings
    AWS_ACCESS_KEY_ID = load_secret('aws_s3_access_key_id.txt')
    AWS_SECRET_ACCESS_KEY = load_secret('aws_s3_secret_access_key.txt')
    AWS_STORAGE_BUCKET_NAME = load_secret('aws_s3_storage_bucket_name.txt')
    AWS_DEFAULT_ACL = 'public-read'
    AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
    AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}
    # s3 static settings
    STATIC_LOCATION = 'static'
    STATIC_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/{STATIC_LOCATION}/'
    STATICFILES_STORAGE = 'games.storage_backends.StaticStorage'
    # s3 public media settings
    PUBLIC_MEDIA_LOCATION = 'media'
    MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/{PUBLIC_MEDIA_LOCATION}/'
    DEFAULT_FILE_STORAGE = 'games.storage_backends.PublicMediaStorage'
else:
    STATIC_URL = '/static/'
    STATIC_ROOT = os.path.join(BASE_DIR, 'static')
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media/')

STATICFILES_DIRS = (
    os.path.join(BASE_DIR, 'static_root'),
)

STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
)

# Authentication

AUTHENTICATION_BACKENDS = (
 'django.contrib.auth.backends.ModelBackend',
 'allauth.account.auth_backends.AuthenticationBackend',
 )

if IS_PROD:
    SITE_ID = 2
else:
    SITE_ID = 1

LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

SOCIALACCOUNT_PROVIDERS = {
    'vk': {
        'SCOPE': [
            'profile',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        }
    },
    'interoves-telegram': {
        'TOKEN': load_secret('telegram_token.txt'),
        'domain': 'https://fat-owl-8.loca.lt/',
        'size': 'small',
        'request_access': 'write'
    }
}

TELEGRAM_BOT_NAME = 'interoves_bot'
TELEGRAM_BOT_TOKEN = load_secret('telegram_token.txt')
TELEGRAM_LOGIN_REDIRECT_URL = 'fat-owl-8.loca.lt'

ACCOUNT_ADAPTER = 'games.users.allauth.AccountAdapter'

TEMPLATE_CONTEXT_PROCESSORS = (
    "django.core.context_processors.auth",
    "django.core.context_processors.debug",
    "django.core.context_processors.i18n",
    "django.core.context_processors.media",
    "django.core.context_processors.request",
)

# CORS Policy

CORS_ORIGIN_ALLOW_ALL = False

CORS_ALLOW_HEADERS = (
   'Access-Control-Allow-Headers',
   'Access-Control-Allow-Credentials',
)

CORS_ORIGIN_WHITELIST = [
    'https://interoves-django-static.s3.amazonaws.com',
]

# Logging

LOGGING = {
    'version': 1,
    'handlers': {
        'stderr': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'stream': sys.stderr,
        }
    },
    'loggers': {
        'application': {
            'handlers': ['stderr'],
            'level': 'INFO',
        }
    }
}

# Inline Editor
INLINEEDIT_ADAPTORS = {
    "person-adaptor": "games.adaptors.PersonAdaptor",
}