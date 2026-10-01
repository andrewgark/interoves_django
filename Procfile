# EB streams web.stdout.log to CloudWatch. Keep Django/application stderr in
# that same stream so an HTTP 500 can be correlated with the access log.
web: daphne -b 0.0.0.0 -p 8000 --application-close-timeout 5 interoves_django.asgi:application 2>&1
