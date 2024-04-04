import requests
import os
import sys
import json
import time
import datetime
import sqlite3
import logging
import subprocess
import xml.etree.ElementTree as ET

# Configure the logging module
logging.basicConfig(level=logging.INFO , format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create a logger
logger = logging.getLogger('collect_logger')

#Getting environment variables
uco_ontology = os.environ['UCO_ONTO_PATH']
root_folder = os.environ['ROOT_FOLDER']
vol_path = os.environ['VOL_PATH']

# Import ontology updater script
sys.path.append(os.path.join(root_folder, "/process"))
from process import ontology_updater

# Import graph updater script
from process import graph_updater

# function to collect data from cve.mitre.org
def cve_init():

    vol_path = os.environ['VOL_PATH']
    # Define the relative path to the data file
    cve_db_file = os.path.join(vol_path, 'cve_database.db')
    with sqlite3.connect(cve_db_file) as conn:
        uco_ontology = os.environ['UCO_ONTO_PATH']
        root_folder = os.environ['ROOT_FOLDER']
        vol_path = os.environ['VOL_PATH']
        # Create database cursor
        cursor = conn.cursor()

        logger.info("############################################################################")
        logger.info("Starting data extraction of CVE data from National Vulnerability Database")
        logger.info("############################################################################\n")

        start_index = 0

        # Check if the meta table exists
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='cve_meta'")
        table_exists = cursor.fetchone()
        if table_exists:
            logger.info("Reading cve_meta table data...")
            cursor.execute(f"SELECT init_finished FROM cve_meta")
            row = cursor.fetchone()
            init_finished = row[0]
            if init_finished == 1:
                logger.info("###############################################")
                logger.info("CVE initializtion already complete exiting now")
                logger.info("###############################################\n")
                return
            cursor.execute(f"SELECT offset FROM cve_meta")
            row = cursor.fetchone()
            start_index = row[0]
        else: 
            logger.info("Table cve_meta does not exist. Creating table...")
            # Create cve_meta table
            cursor.execute('''CREATE TABLE IF NOT EXISTS cve_meta 
                            (id INTEGER PRIMARY KEY, offset INTEGER, last_modified TEXT, init_finished INTEGER DEFAULT 0)''')
            current_time = format_datetime_string(str(datetime.datetime.now()))
            cursor.execute("INSERT INTO cve_meta (id, offset, last_modified) VALUES (?, ?, ?)", (12345, start_index, current_time))
            conn.commit()
            
        logger.info(f"Reading in cve data starting with index {start_index}...")

        # get the data from the website
        cve_api_url = "https://services.nvd.nist.gov/rest/json/cves/2.0?startIndex=" 
        logger.info(f"{cve_api_url}{start_index}&resultsPerPage=2000")
        response = requests.get(f"{cve_api_url}{start_index}&resultsPerPage=2000")
        init_finished = False 
        original_offset = start_index

        cves = {"cves": []}
        cwe_id_list = get_cwe_id_list()
        # logger.info(cwe_id_list)
        while (response.status_code == 200 or response.status_code == 403 or response.status_code == 503) and init_finished == False:
            early_exit = False
            if response.status_code == 403 or response.status_code == 503:
                for i in range(4):
                    if i == 3:
                        logger.info("Unable to recieve response from API, saving results and exiting...")
                        early_exit = True

                    logger.info(f"Retry #{i + 1}: Waiting for 10 seconds, due to API throttling...")
                    time.sleep(10)
                    response = requests.get(f"{cve_api_url}{start_index}&resultsPerPage=2000")
                    if response.status_code == 200:
                        logger.info("Retry Successful! Continuing processing...")
                        break

            if early_exit:
                break
            
            begining_index = start_index
            json_data = response.json()

            # Check if we are done after this loop
            vul_count = len(json_data["vulnerabilities"])

            if vul_count < 2000:
                init_finished = True

            for cve in json_data["vulnerabilities"]:
                cwes = []
                try: 
                    for weakness in  cve['cve']['weaknesses']:
                        for desc in weakness['description']:
                            weakness_value = desc['value'].strip()
                            if weakness_value in cwe_id_list:
                                logger.info(f"Found CWE match for CVE: {cve['cve']['id']} - hasCWE -> {str(desc['value'])}")
                                cwes.append({"cwe": {"id": desc['value']}})
                except Exception:
                    pass

                start_index += 1

                cves["cves"].append({"cve":{
                    "id": cve['cve']["id"],
                    "lastModified":cve['cve']["lastModified"],
                    "published":cve['cve']["published"],
                    "descriptions": cve['cve']['descriptions'],
                    "cwes": cwes
                    }})

            with open("./data/cve/cves.json", "w") as json_file:
                json.dump(cves, json_file, indent=4)
                logger.info("successfully wrote to cves.json")

            successfully_mapped = call_mapper_update("cve")
            if successfully_mapped:
                call_ontology_updater()



            current_time = format_datetime_string(str(datetime.datetime.now()))
            cursor.execute("UPDATE cve_meta SET offset=?, last_modified=? WHERE id=12345", (start_index, current_time))
            conn.commit()
            logger.info(f"Completed batch with startIndex={begining_index}")

            # Wait 5 seconds to avoid throttling
            time.sleep(5)

            response = requests.get(f"{cve_api_url}{start_index}&resultsPerPage=2000")

        
        if init_finished == True:
            cursor.execute("UPDATE cve_meta SET init_finished=1 WHERE id=12345")
            conn.commit()

        total_records = start_index 
        records_added = total_records - original_offset

        logger.info("############################")
        logger.info("Data extraction completed")
        logger.info("############################\n")
        logger.info(f"Database Meta-Table: cves_meta")
        logger.info(f"Total Records: {total_records}")
        logger.info(f"Records Added: {records_added}")
        logger.info(f"Database initialization finished: {init_finished}\n")


def cve_update():
    logger.info("Not added yet...")

# This funnction is used to determine if the cve table initialization is complete init (1), not complete init (0), not started yet (3), or complete init and dataload into neo4j
def check_cve_status():
    # Get the directory of the currently executing script (sub_script.py)
    vol_path = os.environ['VOL_PATH']
    # Define the relative path to the data file
    cve_db_file = os.path.join(vol_path, 'cve_database.db')
    with sqlite3.connect(cve_db_file) as conn:
        # Create database cursor
        cursor = conn.cursor()
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='cve_meta'")
        table_exists = cursor.fetchone()
        logger.info("Here I am....")
        if table_exists:
            logger.info("Table exists...")
            cursor.execute(f"SELECT init_finished FROM cve_meta")
            row = cursor.fetchone()
            init_finished = row[0]
            return init_finished
        else:
            logger.info("Table does not exist....")
            return 3

def check_cwe_status():
    # Need to add the meta-data table
    return 3

def cwe_init():
    # Parse the XML file
    xml_file_path = './data/cwe/cwe_dict.xml'

    # Define the path to the target elements
    target_path = {
        'Weaknesses': './{http://cwe.mitre.org/cwe-7}Weaknesses',
        'Weakness': './{http://cwe.mitre.org/cwe-7}Weakness',
        'ucodescription': './{http://cwe.mitre.org/cwe-7}Description',
        'ucocommonConsequences': './{http://cwe.mitre.org/cwe-7}Common_Consequences',
        'contentHistory': './{http://cwe.mitre.org/cwe-7}Content_History',
        'submission': './{http://cwe.mitre.org/cwe-7}Submission',
        'ucotimeOfIntroduction': './{http://cwe.mitre.org/cwe-7}Submission_Date',
        'ucocweSummary': './{http://cwe.mitre.org/cwe-7}Description',
        'ucocweExtendedSummary': './{http://cwe.mitre.org/cwe-7}Extended_Description',
        'ucocweID': 'ID',
        'ucocweName': 'Name'
    }

    tree = ET.parse(xml_file_path)
    root = tree.getroot()

    # List to hold the extracted CWEs
    cwes = {"cwes": []}

    # Navigate through the XML tree and extract elements
    for weaknesses in root.findall(target_path['Weaknesses']):
        for weakness in weaknesses.findall(target_path['Weakness']):
            id_value = str(weakness.get(target_path['ucocweID']))
            id_value = "CWE-" + id_value.strip()
            description = weakness.find(target_path['ucodescription'])
            if description is not None:
                description = description.text
            common_consequences = weakness.find(target_path['ucocommonConsequences'])
            if common_consequences is not None:
                common_consequences = str(ET.tostring(common_consequences))
            time_of_introduction = None
            content_history = weakness.find(target_path['contentHistory'])
            if content_history is not None:
                submission = content_history.find(target_path['submission'])
                if submission is not None:
                    time_of_introduction = submission.find(target_path['ucotimeOfIntroduction']).text
            summary = weakness.find(target_path['ucocweSummary'])
            if summary is not None:
                summary = summary.text
            name = weakness.get(target_path['ucocweName'])
            extended_summary = weakness.find(target_path['ucocweExtendedSummary'])
            if extended_summary is not None:
                if len(extended_summary.findall("./")) == 0:
                    extended_summary = extended_summary.text
                else:
                    extended_summary = str(ET.tostring(extended_summary))

            # print(f"id_value: {id_value}")
            # print(f"description: {description}")
            # print(f"common_consequences: {common_consequences}")
            # print(f"time_of_introduction: {time_of_introduction}")
            # print(f"summary: {summary}")
            # print(f"name: {name}")
            # print(f"extended_summary: {extended_summary}")

            cwes['cwes'].append(
                {
                    "cwe": {
                        "id_value": id_value,
                        "description": description,
                        "common_consequences": common_consequences,
                        "time_of_introduction": time_of_introduction,
                        "summary": summary,
                        "name": name,
                        "extended_summary": extended_summary
                    }

                }
            )

    with open("./data/cwe/cwes.json", "w") as json_file:
        json.dump(cwes, json_file, indent=4)

    successfully_mapped = call_mapper_update("cwe")
    if successfully_mapped:
        logger.info("Successfully mapped CWEs to RML mapper")
        call_ontology_updater()

    logger.info("############################")
    logger.info("CWE Data extraction completed")
    logger.info("############################\n")
    # logger.info(f"Database Meta-Table: cves_meta")
    # logger.info(f"Total Records: {total_records}")
    # logger.info(f"Records Added: {records_added}")
    # logger.info(f"Database initialization finished: {init_finished}\n")


def get_cwe_id_list():
    # Parse the XML file
    xml_file_path = './data/cwe/cwe_dict.xml'

    # Define the path to the target elements
    target_path = {
        'Weaknesses': './{http://cwe.mitre.org/cwe-7}Weaknesses',
        'Weakness': './{http://cwe.mitre.org/cwe-7}Weakness',
        'ID': './ID'
    }

    tree = ET.parse(xml_file_path)
    root = tree.getroot()

    # List to hold the extracted IDs
    extracted_ids = []

    # Navigate through the XML tree and extract elements
    for weaknesses in root.findall(target_path['Weaknesses']):
        for weakness in weaknesses.findall(target_path['Weakness']):
            id_value = weakness.get('ID')
            if id_value is not None:
                temp_id = "CWE-" + str(id_value)
                extracted_ids.append(temp_id.strip())
    
    return extracted_ids

    with open("./data/cwe/cwes.json", "w") as json_file:
        json.dump(cwes, json_file, indent=4)

    logger.info("\n############################")
    logger.info("CWE Data extraction completed")
    logger.info("############################\n")
    # logger.info(f"Database Meta-Table: cves_meta")
    # logger.info(f"Total Records: {total_records}")
    # logger.info(f"Records Added: {records_added}")
    # logger.info(f"Database initialization finished: {init_finished}\n")


def call_ontology_updater():
    successfully_updated_ontology = ontology_updater.update_ontology()
    if successfully_updated_ontology:
        logger.info("successfully updated the ontology now going to try to insert into the db")
        graph_updater.update_graph()
    else:
        pass


def call_mapper_update(datasource):
    jar_path = "./mapping/mapper.jar"
    output_file = os.path.join(vol_path, "out.ttl")
    if datasource == "cve":
        mapping_file = "./mapping/cve/cve_rml.ttl"
    elif datasource == "cwe":
        mapping_file = "./mapping/cwe/cwe_rml.ttl"
    else:
        logger.info("Not a valid rml source...")
        return False
    # Construct the command
    command = ["java", "-jar", jar_path, "-m", mapping_file, "-s", "turtle"]

    with open(output_file, "w") as file:
        # Run the command and redirect stdout to the file
        try:
            process = subprocess.Popen(command, stdout=file, stderr=subprocess.PIPE)
            # Wait for the command to complete and capture stderr
            _, stderr = process.communicate()
            if process.returncode != 0:
                logger.error("Error running rml mapping: " + str(stderr.decode()))
                return False
            else:
                logger.info("Command executed successfully, output saved to: " + str(output_file))
                return True
        except Exception as e:
            logger.info("In this error")
            logger.error(e)
    return False
    

def format_datetime_string(datetime_string):
    # Split the string into date and time components
    date_part, time_part = datetime_string.split(" ")

    # Split the time component into seconds and milliseconds
    seconds_part, milliseconds_part = time_part.split(".")

    # Keep only the first 3 decimal places (milliseconds)
    milliseconds_part = milliseconds_part[:3]

    # Concatenate the parts with "T" and the rounded milliseconds
    formatted_datetime = f"{date_part}T{seconds_part}.{milliseconds_part}"

    return formatted_datetime


if __name__ == "__main__":
    if len(sys.argv) > 1:
        data_source = sys.argv[1]
        if data_source == "cve_init":
            cve_init()
        elif data_source == "cve_update":
            cve_update()
        else:
            logger.info("ERROR: invalid data source. Please choose from one of the following: cve_init, cve_update")

    else:
        logger.info("Please provide a data source to update(example:python collect.py cve).")

