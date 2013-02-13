"""
Copyright (c) 2011 Socrata.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import json
import logging
import ConfigParser
import time
from datetime import datetime
from urllib import urlencode
from urlparse import urljoin
from time import sleep
from collections import namedtuple

import re
import requests
from os.path import split, expanduser


id_pattern = re.compile('^[0-9a-z]{4}-[0-9a-z]{4}$')
HTTP_DEBUG = False

def column_spec(name, datatype):
    """ convenience function for defining column blueprints in python """
    return {'name': name, 'datatype': datatype}


class SocrataImporter(object):
    """ supports the Socrata upload process """

    def __init__(self, socrata):
        self.socrata = socrata

    def upload(self, path):
        f = open(path)
        filename = split(path[1])

        return self.socrata._request('/imports2?method=scan', 'POST', files={filename: f})

    def import_file(self, name, fileId, blueprint=None, translation=None, skip=0, to_view=None, method='replace'):
        postdata = {'name': name, 'fileId': fileId, 'skip': skip}
        uri = '/imports2.json'
        if to_view:
            uri += '?method=%s' % method
            postdata['viewUid'] = to_view
        if blueprint:
            postdata['blueprint'] = blueprint
        if translation:
            postdata['translation'] = translation
        response = self.socrata._request(uri, 'POST', data=postdata, encoder=None,
            content_type="application/x-www-form-urlencoded")


date_format = '%Y-%m-%dT%H:%M:%S'
default_factories_by_type = {
    'number': int,
    'money': float,
    # formatted like #2013-01-23T15:23:00
    'calendar_date': lambda dtstr: datetime.fromtimestamp(time.mktime(time.strptime(dtstr, date_format))),
    # seconds since Unix epoch
    'date': lambda ts: datetime.fromtimestamp(int(ts)) if ts else None


}

class SocrataBase:
    """Base class for all Socrata API objects"""

    def __init__(self, host=None, username=None, password=None, app_token=None):
        """
        Initializes a new Socrata API object with configuration
        options specified in standard ConfigParser format
        """

        self.username = username
        self.password = password
        self.host = host
        self.app_token = app_token

        cfg = ConfigParser.ConfigParser()
        cfg.read(['socrata.cfg', expanduser('~/.socrata.cfg')])

        if not self.username:
            self.username = cfg.get('credentials', 'user')

        if not self.password:
            self.password = cfg.get('credentials', 'password')

        if not self.host:
            self.host = cfg.get('server', 'host')

        if not self.app_token:
            self.app_token = cfg.get('credentials', 'app_token')

        self.importer = SocrataImporter(self)

    def _request(self, service, type='GET', data={}, files=dict(), encoder=json.dumps, content_type='application/json'):
        """Generic HTTP request, encoding data as JSON and decoding the response"""

        client = getattr(requests, type.lower())
        uri = urljoin(self.host, service)
        headers = {'Content-type': content_type,
                   'X-App-Token': self.app_token}

        if len(files) > 0:
            del headers['Content-type']

        if data and encoder:
            data = encoder(data)

        if not data:
            data = None

        if HTTP_DEBUG:
            logging.warning('%s: %s' % (type, uri))

        response = client(uri,
            headers=headers,
            auth=(self.username, self.password),
            data=data,
            files=files)

        content = response.text
        if HTTP_DEBUG:
            logging.warning(content)
        if content != None and len(content) > 0:
            response_parsed = json.loads(content)
            if hasattr(response_parsed, 'has_key') and\
               response_parsed.has_key('error') and response_parsed['error'] == True:
                print "Error: %s" % response_parsed['message']
                return response_parsed

            while (
                response.status_code == 202 or (
                hasattr(response_parsed, 'has_key') and response_parsed.has_key('status'))):
                if HTTP_DEBUG:
                    logging.warning("delayed response-- trying again in 5 seonds")
                sleep(5)
                if response_parsed.has_key('ticket'):
                    response = requests.get(
                        urljoin(self.host, '/api/imports2.json?ticket=%s' % response_parsed['ticket']),
                        headers=headers,
                        auth=(self.username, self.password))
                    response_parsed = json.loads(response.text)
                else:
                    response = client(uri,
                        headers=headers,
                        auth=(self.username, self.password),
                        data=data,
                        files=files)

                if HTTP_DEBUG:
                    logging.warning(response.text)

                response_parsed = json.loads(response.text)
            return response_parsed
        return None

    def _batch(self, data):
        payload = {'requests': data}
        return self._request('/batches', 'POST', payload)


class Dataset(SocrataBase):
    """Represents a Socrata Dataset, can be used for CRUD and more"""

    # Fetch columns
    def columns(self):
        return self._request("/views/%s/columns.json" % self.id, 'GET')

    # Creates a new column, POSTing the request immediately
    def add_column(self, name, description='', type='text',
                   hidden=False, rich=False, width=100):
        if not self.attached():
            return False
        data = {'name': name, 'dataTypeName': type,
                'description': description, 'hidden': hidden,
                'width': width}
        if rich:
            data['format'] = {'formatting_option': 'Rich'}
        response = self._request("/views/%s/columns.json" % self.id,
            'POST', data)
        return response

    # Creates a new column, POSTing the request immediately
    # TODO: this seems broken in the master branch since these function parameters were not defined there
    def delete_column(self, column_id, name, description='', type='text',
                      hidden=False, rich=False, width=100):
        if not self.attached():
            return False
        data = {'name': name, 'dataTypeName': type,
                'description': description, 'hidden': hidden,
                'width': width}
        if rich:
            data['format'] = {'formatting_option': 'Rich'}
        response = self._request("/views/%s/columns.json" % self.id,
            'POST', data)
        return response

    # Adds a new row by specifying an array of cells
    def add_row(self, data):
        if not self.attached():
            return False
        response = self._request("/views/%s/rows.json" % self.id,
            'POST', data)

    # For batch row importing, returns a dict to be POST'd
    # Takes same array of cells as add_row
    def add_row_delayed(self, data):
        if not self.attached():
            return False
        return {'url': "/views/%s/rows.json" % self.id,
                'requestType': 'POST',
                'body': json.dumps(data)}

    # Retrieves all rows, or optionally just the ID's
    def rows(self, row_ids_only=False):
        uri = '/views/%s/rows.json' % self.id
        if row_ids_only:
            uri += '?row_ids_only=true'

        return self._request(uri, 'GET')['data']

    def paging_row_gen(self, row_limit=None):
        """
        a generator that returns rows from the data set in a paged manner, up to row_limit.  If row_limit=None, then no
        limit is applied and all the rows in the data set will eventually be generated
        """
        rows_read = 0
        # this is the chunk size to use when fetching rows, Socrata max seems to be 1000
        row_chunk_size = 1000
        while True:
            this_chunk = row_chunk_size
            if (not row_limit == None and
                rows_read + row_chunk_size > row_limit):
                this_chunk = row_limit - rows_read
                #use the rows API method to retrieve only data rows, with start and length params
            uri = '/views/{}/rows.json?method=getRows&start={}&length={}'.format(self.id, rows_read, this_chunk)
            rows = self._request(uri, 'GET')
            num_rows = len(rows)
            if (num_rows == 0):
                # no rows in this page, we are finished
                break
            else:
                for row in rows:
                    yield row
                    rows_read += 1
                if (not row_limit == None and rows_read >= row_limit):
                    # we've reached the end of the results
                    break

    def typed_paged_rows(self, row_limit=None, type_name=None, factories_by_type=default_factories_by_type):
        """
        Returns rows from the data set as collections.namedtuple instances, with the given type_name and row_limit.  The
        namedtuple instances will have keys corresponding to the data set 'fieldName's for each column, and values
        corresponding to the row values for those fields.
        This will invoke both the columns (first, to determine types) and then getRows (to build the values) APIs from
        the data set.  It uses paging via the rows API to read the data in chunks.

        If row_limit=None then all rows from the data set will eventually be generated.

        factories_by_type is a map of functions to build the values from data rows for a given Socrata type.  The key
        should be the Socrata type name (corresponding to "dataTypeName" in the columns JSON API).  The value should be
        a function that takes the value for the field whose type matches the key name, and returns the value.  The
        purpose of this is to convert Socrata data values to corresponding Python values where appropriate
        (ex: datetime).  If this is not given, a default dictionary will be used that maps the following Socrata types:
        'number' (to an int), 'money' (to an int), 'calendar_date' to a datetime based on a default formatting, and
        'date' to a datetime based on a default formatting
        """
        if (not type_name):
            type_name = 'UnknownType'

        cols = self._request('/views/{}/columns.json'.format(self.id), 'GET')
        column_id_to_col_data = {}
        field_names = set()
        for column in cols:
            field_fn = lambda x: x
            if (column['dataTypeName'] in factories_by_type):
                field_fn = factories_by_type[column['dataTypeName']]
            field_names.add(column['fieldName'])
            column_id_to_col_data[str(column['id'])] = (column['fieldName'], field_fn)

        row_type = namedtuple(type_name, field_names)

        for row in self.paging_row_gen(row_limit=row_limit):
            row_data = {}
            for col_id, col_def in column_id_to_col_data.iteritems():
                field_name, field_val_fn = col_def
                if (not col_id in row):
                    row_data[field_name] = None
                else:
                    col_val = row[col_id]
                    if (not col_val == None):
                        row_data[field_name] = field_val_fn(col_val)
                    else:
                        row_data[field_name] = None
            yield row_type(**row_data)


    def typed_rows(self, row_limit=None, type_name=None, factories_by_type=default_factories_by_type):
        """
        Returns rows from the data set as collections.namedtuple instances, with the given type_name and row_limit.  The
        namedtuple instances will have keys corresponding to the data set 'fieldName's for each column, and values
        corresponding to the row values for those fields.
        This will invoke the rows API on the data set which will return metadata (including column types) and row data
        in one response.  Without overridding row_limit, all the rows in the data set will therefore be returned at
        once by this method, which may take a very long time depending on the size of the data set.

        If row_limit=None then all rows from the data set will eventually be generated.

        factories_by_type is a map of functions to build the values from data rows for a given Socrata type.  The key
        should be the Socrata type name (corresponding to "dataTypeName" in the columns JSON API).  The value should be
        a function that takes the value for the field whose type matches the key name, and returns the value.  The
        purpose of this is to convert Socrata data values to corresponding Python values where appropriate
        (ex: datetime).  If this is not given, a default dictionary will be used that maps the following Socrata types:
        'number' (to an int), 'money' (to an int), 'calendar_date' to a datetime based on a default formatting, and
        'date' to a datetime based on a default formatting
        """
        uri = '/views/{}/rows.json{}'.format(self.id, '?max_rows=' + str(row_limit) if row_limit else '')
        resp = self._request(uri, 'GET')
        cols = resp['meta']['view']['columns']

        def ident_fn(x):
            return x

        fields = []
        field_names = set()
        meta_col_idx = 0
        for col_num, column in enumerate(sorted(cols, key=lambda col: col['position'])):
            if (column['dataTypeName'] == 'meta_data'):
                if (column['name'] == 'meta'):
                    meta_col_idx = col_num
                    #dummy place in list so that lookup below is simpler
                    fields.append(None)
                else:
                    field_name = 'meta_' + column['name']
                    field_names.add(field_name)
                    fields.append((field_name, str))
            else:
                field_name = column['fieldName']
                field_names.add(field_name)
                field_fn = ident_fn
                if (column['dataTypeName'] in factories_by_type):
                    field_fn = factories_by_type[column['dataTypeName']]
                fields.append((field_name, field_fn))

        if (not type_name):
            if ('resourceName' in resp['meta']['view']):
                type_name = resp['meta']['view']['resourceName']
            else:
                type_name = 'UnknownType'
        row_type = namedtuple(type_name, field_names)

        for row in resp['data']:
            row_data = {}
            for col_idx, col_val in enumerate(row):
                if (not col_idx == meta_col_idx):
                    field_name, field_val_fn = fields[col_idx]
                    #try:
                    if (not col_val == None):
                        row_data[field_name] = field_val_fn(col_val)
                    else:
                        row_data[field_name] = None
                        #except BaseException as :
                        #    row_data[field_name] = None
            yield row_type(**row_data)

    # deletes a row
    def delete_row(self, row_id):
        return self._request('/views/%s/rows/%s.json' % (self.id, row_id), 'DELETE')

    # _batch'able row deletion
    def delete_row_delayed(self, row_id):
        if not self.attached():
            return False
        return {'url': '/views/%s/rows/%s.json' % (self.id, row_id),
                'requestType': 'DELETE'}

    # Is this class currently associated with an existing dataset?
    def attached(self):
        return self.is_id(self.id)

    # Creates a new dataset by sending a POST request, saves four-four ID
    def create(self, name, description='', tags=[], public=True):
        self.error = False
        data = {'name': name, 'description': description}
        if public:
            data['flags'] = ['dataPublicRead']
        if tags.count > 0:
            data['tags'] = tags

        response = self._request('/views.json', 'POST', data)
        if response.has_key('error'):
            self.error = response['message']
            if response['code'] == 'authentication_required':
                raise RuntimeError('You must specify proper authentication credentials')
            elif response['code'] == 'invalid_request':
                raise DuplicateDatasetError(name)
            else:
                raise RuntimeError('API Error ' + response['message'])
        self.id = response['id']
        return True

    # Deletes the active dataset
    def delete(self):
        self.error = False
        response = self._request("/views.json?id=%s&method=delete" % self.id,
            'DELETE', None)
        return response == None

    # Call the search service with optional params
    def find_datasets(self, params={}):
        self.error = False
        sets = self._request("/api/search/views.json?%s" % urlencode(params), "GET")
        return sets

    def metadata(self):
        self.error = False
        return (self._request("/views/%s.json" % self.id, 'GET'))

    def attachments(self):
        metadata = self.metadata()
        if metadata == None or (not metadata.has_key('attachments')):
            return []
        return metadata['attachments']

    def attach_file(self, filename):
        metadata = self.metadata()
        if not metadata.has_key('attachments'):
            metadata['attachments'] = []

        response = self.multipart_post('/assets', filename)
        if not response.has_key('id'):
            print "Error uploading file to assets service: no ID returned: %s" % response
            return
        attachment = {'blobId': response['id'],
                      'name': response['nameForOutput'],
                      'filename': response['nameForOutput']}
        metadata['attachments'].append(attachment)
        self._request("/views/%s.json" % self.id, 'PUT', {'metadata': metadata})

    # creates a working copy of this dataset
    def create_working_copy(self):
        if self.attached():
            response = self._request("/views/%s/publication.json?method=copy" % self.id, 'POST')
            working_copy = Dataset(self.host, self.username, self.password, self.app_token)
            working_copy.use_existing(response['id'])
            return working_copy

    # publish this working copy
    def publish(self):
        if self.attached():
            response = self._request("/api/views/%s/publication.json" % self.id, 'POST')
            return response

    def multipart_post(self, url, filename, field='file'):
        file_to_upload = open(filename, "rb")
        response = self._request(url, type='POST', files={filename: file_to_upload})
        return response

    def append(self, fileId, name, skip=0, blueprint=None, translation=None):
        return self.importer.import_file(name=name, fileId=fileId, to_view=self.id, method='append', skip=skip,
            blueprint=blueprint, translation=translation)

    def replace(self, fileId, name, skip=0, blueprint=None, translation=None):
        return self.importer.import_file(name=name, fileId=fileId, to_view=self.id, method='replace', skip=skip,
            blueprint=blueprint, translation=translation)

    # Is the string 'id' a valid four-four ID?
    def is_id(self, id):
        return id_pattern.match(id) != None

    def use_existing(self, id):
        if self.is_id(id):
            self.id = id
        else:
            return False

    def short_url(self):
        return urljoin(self.host, "/d/" + str(self.id))


class DuplicateDatasetError(Exception):
    """Raised if a dataset with the specified name already exists"""

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return "Duplicate dataset with name '%s'" % self.name