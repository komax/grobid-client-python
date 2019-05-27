#!/usr/bin/env python3
import sys
import os
import io
import json
import argparse
import time
import concurrent.futures
import glob
import logging
from client import ApiClient
import ntpath

'''
This version uses the standard ProcessPoolExecutor for parallelizing the concurrent calls to the GROBID services. 
Given the limits of ThreadPoolExecutor (input stored in memory, blocking Executor.map until the whole input
is acquired), it works with batches of PDF of a size indicated in the config.json file (default is 1000 entries). 
We are moving from first batch to the second one only when the first is entirely processed - which means it is
slightly sub-optimal, but should scale better. However acquiring a list of million of files in directories would
require something scalable too, which is not implemented for the moment.   
'''
class grobid_client(ApiClient):

    def __init__(self, config_path='./config.json'):
        self.config = None
        self._load_config(config_path)

    def _load_config(self, path='./config.json'):
        """
        Load the json configuration 
        """
        config_json = open(path).read()
        self.config = json.loads(config_json)

    def process(self, input, output, n, service, generateIDs, consolidate_header, consolidate_citations, error_log=False):
        print(error_log)

        # Set up logger for errors if needed.
        if error_log:
            logging.basicConfig(filename='errors.log', level=logging.WARNING)
        batch_size_pdf = self.config['batch_size']
        pdf_files = []

        for pdf_file in glob.glob(input + "/*.pdf"):
            pdf_files.append(pdf_file)

            if len(pdf_files) == batch_size_pdf:
                self.process_batch(pdf_files, output, n, service, generateIDs, consolidate_header, consolidate_citations, error_log)
                pdf_files = []

        # last batch
        if len(pdf_files) > 0:
            self.process_batch(pdf_files, output, n, service, generateIDs, consolidate_header, consolidate_citations, error_log)

    def process_batch(self, pdf_files, output, n, service, generateIDs, consolidate_header, consolidate_citations, error_log):
        print(len(pdf_files), "PDF files to process")
        #with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
        with concurrent.futures.ProcessPoolExecutor(max_workers=n) as executor:
            for pdf_file in pdf_files:
                executor.submit(self.process_pdf, pdf_file, output, service, generateIDs, consolidate_header, consolidate_citations, error_log)

    def process_pdf(self, pdf_file, output, service, generateIDs, consolidate_header, consolidate_citations, error_log):
        # check if TEI file is already produced 
        # we use ntpath here to be sure it will work on Windows too
        pdf_file_name = ntpath.basename(pdf_file)
        filename = os.path.join(output, os.path.splitext(pdf_file_name)[0] + '.tei.xml')
        if os.path.isfile(filename):
            return

        print(pdf_file)
        files = {
            'input': (
                pdf_file,
                open(pdf_file, 'rb'),
                'application/pdf',
                {'Expires': '0'}
            )
        }
        
        the_url = 'http://'+self.config['grobid_server']
        if len(self.config['grobid_port'])>0:
            the_url += ":"+self.config['grobid_port']
        the_url += "/api/"+service
        #print(the_url)

        # set the GROBID parameters
        the_data = {}
        if generateIDs:
            the_data['generateIDs'] = '1'
        if consolidate_header:
            the_data['consolidateHeader'] = '1'
        if consolidate_citations:
            the_data['consolidateCitations'] = '1'    

        res, status = self.post(
            url=the_url,
            files=files,
            data=the_data,
            headers={'Accept': 'text/plain'}
        )

        #print(str(status))
        #print(res.text)

        # FIXME Implement a better error handling and report failed pdfs to a logger.
        if status == 503:
            time.sleep(self.config['sleep_time'])
            return self.process_pdf(pdf_file, output)
        if status == 200:
            # Successful.
            # writing TEI file
            with io.open(filename,'w',encoding='utf8') as tei_file:
                tei_file.write(res.text)
        else:
            print('Processing failed with error ' + str(status))
            if error_log:
                logging.warning('Failed to process %s with status %d', pdf_file, status)


def test():
    client = grobid_client()
    # do some test...

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = "Client for GROBID services")
    parser.add_argument("service", help="one of [processFulltextDocument, processHeaderDocument, processReferences]")
    parser.add_argument("--input", default=None, help="path to the directory containing PDF to process") 
    parser.add_argument("--output", default=None, help="path to the directory where to put the results") 
    parser.add_argument("--config", default="./config.json", help="path to the config file, default is ./config.json") 
    parser.add_argument("--n", default=10, help="concurrency for service usage") 
    parser.add_argument("--generateIDs", action='store_true', help="generate random xml:id to textual XML elements of the result files") 
    parser.add_argument("--consolidate_header", action='store_true', help="call GROBID with consolidation of the metadata extracted from the header") 
    parser.add_argument("--consolidate_citations", action='store_true', help="call GROBID with consolidation of the extracted bibliographical references")
    parser.add_argument("--error-log", default=False, action='store_true', help="create a log file for all pdfs which cannot be handled") 

    args = parser.parse_args()

    input_path = args.input
    config_path = args.config
    output_path = args.output
    n =1
    try:
        n = int(args.n)
        assert n > 0, "Concurrency parameter needs to be positive."
    except ValueError:
        print("Invalid concurrency parameter n:", n, "n = 10 will be used by default")

    service = args.service
    generateIDs = args.generateIDs
    consolidate_header = args.consolidate_header
    consolidate_citations = args.consolidate_citations
    error_log = args.error_log

    client = grobid_client(config_path=config_path)

    start_time = time.time()

    client.process(input_path, output_path, n, service, generateIDs, consolidate_header, consolidate_citations, error_log)

    runtime = round(time.time() - start_time, 3)
    print("runtime: %s seconds " % (runtime))
