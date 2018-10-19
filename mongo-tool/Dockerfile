FROM debian
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv 7F0CEB10
RUN echo 'deb http://downloads-distro.mongodb.org/repo/debian-sysvinit dist 10gen' | tee /etc/apt/sources.list.d/mongodb.list
RUN apt-get update -y && apt-get install -y mongodb-org-tools=2.6.9 python jq python-pip
RUN pip install awscli

ADD . /mongo-restore
WORKDIR /mongo-restore
RUN pip install -r /mongo-restore/requirements.txt
ENTRYPOINT ["/usr/bin/mongorestore"]
CMD ["--help"]
