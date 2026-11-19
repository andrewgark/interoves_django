# Nutrimatic search Lambda. Built by scripts/deploy_nutrimatic_lambda.sh from a
# context it assembles (the index is not in git; it comes from S3).
FROM public.ecr.aws/lambda/python:3.12

# find-expr is a C++ binary built for glibc 2.34 (AL2023, same as Green).
RUN dnf install -y libstdc++ && dnf clean all

COPY wiki-merged.packed.index ${LAMBDA_TASK_ROOT}/
COPY find-expr cgi-search.py handler.py ${LAMBDA_TASK_ROOT}/
RUN chmod 0755 ${LAMBDA_TASK_ROOT}/find-expr && chmod 0644 ${LAMBDA_TASK_ROOT}/wiki-merged.packed.index

CMD ["handler.handler"]
