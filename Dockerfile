FROM gradle:8.6.0-jdk17-alpine AS build
COPY . /home/gradle/Crabstero
WORKDIR /home/gradle/Crabstero
RUN gradle build --no-daemon

FROM eclipse-temurin:17.0.10_7-jre-alpine AS run
COPY --from=build /home/gradle/Crabstero/build/distributions/Crabstero.tar /
RUN tar -xvf Crabstero.tar && rm /Crabstero.tar /Crabstero/bin/Crabstero.bat
ENTRYPOINT ["/Crabstero/bin/Crabstero"]
