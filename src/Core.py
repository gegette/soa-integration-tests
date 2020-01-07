# -*- coding: utf-8 -*-
import pyodbc
import psycopg2
import time
import requests
import json
import difflib
import csv
import re
import os
import time
from tqdm import tqdm
import sys, getopt
from openpyxl import Workbook
import src.Config as cfg
import src.Log as log
import urllib3

# Disable REST InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class TestResult:
    def __init__(self, n="", s="", e="", g=""):
        self.testName = n
        self.status = s
        self.expectedResult = e
        self.gottenResult = g


def runSQLUpdate(environnement, server, operation):
    query = operation["command"].format(ENV_NAME=environnement["name"])

    if server == "DB2":
        conn = pyodbc.connect(environnement["servers"][server])
    elif server == "POSTGRE":
        conn = psycopg2.connect(environnement["servers"][server])

    cursor = conn.cursor()
    log.debug("Executing %s update: %s" %(server, query))

    with conn:
        cursor.execute(query)


def runSQLCheck(environnement, test):
    server = test["out"]["server"]
    query = test["out"]["operation"]["command"].format(ENV_NAME=environnement["name"])

    testResult = TestResult()
    testResult.testName = test["name"]
    testResult.command = test["out"]["operation"]["command"]
    testResult.expectedResult = test["out"]["expected"]["value"]
    testResult.gottenResult = "Test check failed"
    testResult.status = "KO"

    if server == "DB2":
        conn = pyodbc.connect(environnement["servers"][server])
    elif server == "POSTGRE":
        conn = psycopg2.connect(environnement["servers"][server])

    cursor = conn.cursor()
    log.debug("Executing %s select: %s" %(server, query))

    with conn:
        cursor.execute(query)
        rows = cursor.fetchall()
        log.debug("Results: %s" %str(rows))
        
        if len(rows) > 0 and len(rows[0]) > 0:
            testResult.gottenResult = str(rows[0][0])

            if testResult.gottenResult == testResult.expectedResult:
                testResult.status = "OK"
                log.info("%s" %testResult.status)
            else:
                testResult.status = "KO"
                log.error("%s" %testResult.status)
                log.error("Expected %s" %(testResult.expectedResult))
                log.error("Gotten %s" %(testResult.gottenResult))
        else:
            testResult.gottenResult = "No result"
            testResult.status = "KO"
            log.error("%s - %s" %(testResult.status, testResult.gottenResult))

    return testResult


def runRESTPost(environnement, server, operation):
    log.debug(cfg.GPP_VERSION)
    restCommand = operation["command"].format(CLI_VERSION=cfg.CLI_VERSION, DIF_VERSION=cfg.DIF_VERSION, GPP_VERSION=cfg.GPP_VERSION)
    url = environnement["servers"][server] + restCommand
    data = operation["data"]

    response = requests.post(url, json=data, verify=False, headers={'Authorization': '{0}'.format(cfg.GODMODE_TOKEN)})
    log.debug("Executing POST %s with data %s" %(url, data))
    log.debug("Result: %s" %(response.text))
    log.debug("Status code: %s" %(response.status_code))

    if not (response.status_code >= 200 and response.status_code < 300):
        log.error("Status code: %s" %(response.status_code))


def runRESTCheck(environnement, test):
    server = test["out"]["server"]
    restCommand = test["out"]["operation"]["command"].format(CLI_VERSION=cfg.CLI_VERSION, DIF_VERSION=cfg.DIF_VERSION, GPP_VERSION=cfg.GPP_VERSION)
    url = environnement["servers"][server] + restCommand

    testResult = TestResult()
    testResult.testName = test["name"]
    testResult.command = test["out"]["operation"]["command"]
    testResult.expectedResult = test["out"]["expected"]["value"]
    testResult.gottenResult = "Test check failed"
    testResult.status = "KO"

    response = requests.get(url, params=None, verify=False, headers={'Authorization': '{0}'.format(cfg.GODMODE_TOKEN)})
    log.debug("Executing GET %s: %s" %(url, response.text))
    log.debug("Status code: %s" %(response.status_code))
    
    if response.ok:
        jsonResult = response.json()
        
        # Parsing de l'attribut à controler
        expectedAttribute = test["out"]["expected"]["attribute"]
        for p in expectedAttribute:
            jsonResult = jsonResult[p]

        testResult.gottenResult = jsonResult

        if testResult.gottenResult == testResult.expectedResult:
            testResult.status = "OK"
            log.info("%s" %testResult.status)
        else:
            testResult.status = "KO"
            log.error("%s" %testResult.status)
            log.error("Expected %s" %(testResult.expectedResult))
            log.error("Gotten %s" %(testResult.gottenResult))
    else:
        testResult.gottenResult = response.status_code
        testResult.status = "KO"
        log.error("%s - %s" %(testResult.status, testResult.gottenResult))

    return testResult


def processJMSResponse(method, response):
    if (response.status_code == 200):
        jsonResponse = json.loads(response.text)
        responseCode = jsonResponse["status"]
        log.debug("%s - HTTP Status %s" %(method, str(jsonResponse["status"])))

        if responseCode != 200:
            log.error(response.text)
            sys.exit(2)

        return jsonResponse
    else:
        log.error(method)
        log.error(response)


def runJMSPost(environnement, operation):
    hostname = environnement["servers"]["JMS"]["hostname"]
    broker = environnement["servers"]["JMS"]["broker"]
    topic = operation["command"]

    # Préparation du message JMS
    message = operation["data"]
    properties = "{ \"PersistentDelivery\":\"true\" }"
    argument = []
    argument.append(properties)
    argument.append(message)
    argument.append(cfg.JMS_USERNAME)
    argument.append(cfg.JMS_PASSWORD)
    jmsMessage = json.dumps(argument)

    # Préparation de l'envoi
    textBody = cfg.JMS_BODY_POST_MESSAGE.replace("[BROKER]", broker).replace("[TOPIC]", topic).replace("[ARGUMENTS]", jmsMessage)
    log.debug(textBody)
    jsonBody = json.loads(textBody)

    # Envoi
    response = requests.post(cfg.JMS_URL_POST_MESSAGE.format(hostname), json=jsonBody, auth=(cfg.JMS_USERNAME, cfg.JMS_PASSWORD))
    return processJMSResponse("postMessage", response)


def runTest(environnement, test):
    log.info("Running test %s" %test["name"])

    # Modification de donnée en entrée
    if test["in"]["type"] == "SQL":
        runSQLUpdate(environnement, test["in"]["server"], test["in"]["operation"])
    elif test["in"]["type"] == "REST":
        runRESTPost(environnement, test["in"]["server"], test["in"]["operation"])
    elif test["in"]["type"] == "JMS":
        runJMSPost(environnement, test["in"]["operation"])

    # WAIT 
    time.sleep(test["sleeptime"])

    # Test en sortie
    if test["out"]["type"] == "SQL":
        result = runSQLCheck(environnement, test)
    elif test["out"]["type"] == "REST":
        result = runRESTCheck(environnement, test)

    # Rollback operation faite en entrée
    if test["rollback"]["type"] == "SQL":
        runSQLUpdate(environnement, test["rollback"]["server"], test["rollback"]["operation"])
    elif test["rollback"]["type"] == "REST":
        runRESTPost(environnement, test["rollback"]["server"], test["rollback"]["operation"])
    elif test["in"]["type"] == "JMS":
        runJMSPost(environnement, test["rollback"]["operation"])

    return result


def exportResults(environnement, results):
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "SoaTestIT - Results"
    ws1.append(cfg.EXCEL_COLUMNS)

    for result in results:
        ws1.append([result.testName, result.status, result.command, result.expectedResult, result.gottenResult])

    pathFolder = os.path.dirname(__file__)[0:len(os.path.dirname(__file__))-4] + '\\' + cfg.OUTPUT_FOLDER
    if not os.path.exists(pathFolder):
        os.mkdir(pathFolder)

    path = pathFolder + environnement["name"] + cfg.EXCEL_FILE_NAME
    log.info("Fichier excel cree : %s" %path)
    wb.save(path)


def getFinalStatus(results):
    errors = 0

    for result in results:
        if result.status != "OK":
            errors = errors + 1
    
    return errors
