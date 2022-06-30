FROM gradle:7.0.0-jdk11 AS build
COPY . /home/gradle/Crabstero
WORKDIR /home/gradle/Crabstero
RUN gradle build --no-daemon

FROM adoptopenjdk/openjdk11-openj9:jre AS run
COPY --from=build /home/gradle/Crabstero/build/distributions/Crabstero.tar /
RUN tar -xvf Crabstero.tar && rm /Crabstero.tar /Crabstero/bin/Crabstero.bat
ENTRYPOINT ["/Crabstero/bin/Crabstero"]