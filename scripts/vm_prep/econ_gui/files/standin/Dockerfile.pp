FROM osci-econ-gui:latest
USER root
COPY pp.tar.gz /tmp/
COPY grader/PPRef.class /opt/grader/ppref/PPRef.class
RUN cd /opt && tar xzf /tmp/pp.tar.gz && rm /tmp/pp.tar.gz  && sed -i "1i -vm\n/opt/jre-jd/bin/java" /opt/portfolio/PortfolioPerformance.ini  && ln -s /opt/portfolio/PortfolioPerformance /usr/local/bin/portfolio  && printf "[Desktop Entry]\nName=Portfolio Performance\nExec=/opt/portfolio/PortfolioPerformance\nType=Application\nTerminal=false\nCategories=Office;\n" > /usr/share/applications/portfolio.desktop  && chown -R root:root /opt/grader && chmod 700 /opt/grader
USER user
