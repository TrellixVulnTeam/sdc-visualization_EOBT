"""Create a visualization server."""

import datetime
import time
import logging
import json
import tempfile
import pathlib
import shutil
import os
import functools

import netCDF4
import numpy as np
import geojson
import pyproj
import pandas as pd
import requests

import webdav3.client

from flask import Blueprint, Flask, Response, jsonify, session, current_app, request, g, redirect
from flask_cors import CORS, cross_origin
from flask_login import LoginManager, login_user, login_manager, current_user, logout_user

import flask.json

from sdc_visualization.ds import get_ds, close_ds
from sdc_visualization.user import User

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

blueprint = Blueprint('public', __name__, static_folder='../static')
# TODO: remove CORS from authentication
CORS(blueprint)

# we  need this at  module level because we need to register functions
login_manager = LoginManager()

# Lookup the relevant URL's
# for now added /
VIZ_URL = os.environ.get('VIZ_URL', 'https://jellyfish.argo.grnet.gr/viz/')
DASHBOARD_URL = os.environ.get('DASHBOARD_URL', 'https://sdc-test.argo.grnet.gr')


# TODO: move some of this out of here...
def antimeridian_cut(lon):
    """longitudes > 180 -> -360"""
    return np.mod(np.array(lon) + 180, 360) - 180


@blueprint.route('/', methods=['GET', 'POST'])
@cross_origin()
def home():
    """Home page."""
    return 'home'


@blueprint.route('/auth', methods=['GET', 'POST'])
@cross_origin()
def auth():
    """Check if user is authenticated."""

    # if user was created  with a service-auth-token
    # get form that was filled in when user was created
    form = getattr(current_user, 'form', None)
    if form:
        token = current_user.form.get('service_auth_token')
    else:
        # we did not get a service_auth_token
        token = None


    # Do a request to see if token is still valid
    if token is not None:
        status = check_token(token)
    else:
        # or we assume it is  valid
        # TODO: remove this if dashboard is setup properly
        status = 200

    if current_user.is_authenticated and status == 200:
        return "You are logged in! Welcome."
    else:
        return 'Sorry, but unfortunately you\'re not logged in.', 401

def check_token(token):
    # check it against the dashboard
    url = DASHBOARD_URL + '/service_auth?service_auth_token=' +  token
    # TODO: change  to post later, check security
    resp = requests.get(url)
    # if we don't have false, or if we did not get a direct response
    if 'false' == resp.text or resp.status_code != 200:
        return 401
    else:
        return 200


@blueprint.route('/login', methods=['POST'])
def login():
    """Login"""

    # You can store extra information like this....
    service_auth_token = request.form.get('service_auth_token')
    # if we get a service_auth_token
    if service_auth_token is not  None:
        status = check_token(service_auth_token)
        if status != 200:
            msg = 'Dashboard authentication at {} failed'.format(DASHBOARD_URL)
            return Response(msg, status=status)


    # Create a new user
    username = request.form['username']
    user = User.get(username)
    # store anything from the form
    user.form.update(request.form)
    # Log him in, now we know the user
    login_user(user)

    # redirect to this directory.
    return redirect(VIZ_URL)


@blueprint.route('/logout', methods=['POST'])
def logout():
    # remove the username from the session if it's there
    logout_user()
    return jsonify({"result": "ok", "message": "user logged out"})

@blueprint.route('/debug', methods=['GET', 'POST'])
@cross_origin()
def debug():
    """Debug page, remove later"""
    debug_info = {
        "session": dict(session),
        "user": current_user.get_id(),
        "form": getattr(current_user, 'form', {})
    }
    return jsonify(dict(debug_info))



@blueprint.route('/health', methods=['GET', 'POST'])
@cross_origin()
def health():
    """Home page."""
    return jsonify({
        "health": "ok"
    })



@blueprint.route('/api/load', methods=['POST'])
@cross_origin()
def load():
    # TODO: validate filename further, otherwise we might load any file
    req_data = request.get_json()

    # the filename string
    filename = req_data.get('filename')

    # the expanded path
    filepath = pathlib.Path(filename).expanduser()

    resp = {"loaded": False}
    if not filepath.suffix == '.nc':
        resp["error"] = "filename does not end in .nc"
        return jsonify(resp)
    if not filepath.exists():
        resp["error"] = "file does not exist"
        return jsonify(resp)
    print(req_data)
    if req_data.get('copy', False):
        tmp_dir = tempfile.mkdtemp(prefix='sdc-', suffix='-remove')
        # copy the expanded file
        shutil.copy(filepath, tmp_dir)
        # replace filename with new filename
        filename = str(pathlib.Path(tmp_dir) / filepath.name)
    # add the dataset to the loaded app
    # perhaps use flask.g, but that did not work
    current_app.filename = filename
    ds = get_ds()
    resp["loaded"] = True
    resp["filename"] = filename
    ds.close()
    return jsonify(resp)


@blueprint.route('/api/dataset', methods=['GET', 'POST'])
@cross_origin()
def dataset():
    """Return dataset metadata."""
    # get the dataset from the current app
    ds = get_ds()
    logger.info('opened %s', ds)
    if ds is None:
        resp = {
            "_comment": "no data loaded"
        }
        return jsonify(resp)
    # this can be a bit slow
    date_nums = ds.variables['date_time'][:]

    def ensure_datetime(maybe_datetime):
        """sometimes < 1582 we get netCDF4 datetimes which have an explicit Gregorian calendar"""

        if hasattr(maybe_datetime, '_to_real_datetime'):
            date = maybe_datetime._to_real_datetime()
        else:
            date = maybe_datetime

        return date
    # if we have an actual range, use that
    try:
        times = netCDF4.num2date(
            ds.variables['date_time'].valid_range,
            ds.variables['date_time'].units
        )
    except AttributeError:
        # use datenums
        times = netCDF4.num2date(
            [date_nums.min(), date_nums.max()],
            ds.variables['date_time'].units
        )

    if times[0].year < 1970:
        times[0] = datetime.datetime(1970, 1, 1)
    resp = {
        "name": ds.filepath(),
        "variables": list(ds.variables.keys()),
        "time_extent": [
            ensure_datetime(times[0]).isoformat(),
            ensure_datetime(times[-1]).isoformat()
        ]
    }
    ds.close()
    return jsonify(resp)


@blueprint.route('/api/extent', methods=['GET', 'POST'])
@cross_origin()
def extent():
    """Return dataset extent."""
    # get the dataset from the current app
    ds = get_ds()
    if ds is None:
        return jsonify({
            'error': 'data not loaded'
        })

    # ensure that our array is always masked
    date_time = np.ma.masked_array(
        ds.variables['date_time'][:]
    )

    t_ini = netCDF4.num2date(
        np.min(date_time[:]),
        ds.variables['date_time'].units
    )
    t_fin = netCDF4.num2date(
        np.max(date_time[:]),
        ds.variables['date_time'].units
    )

    resp = [t_ini.year, t_fin.year]
    ds.close()
    return jsonify(resp)


@functools.lru_cache()
def get_cdi_ids():
    ds = get_ds()
    # this takes 2 seconds
    for var_name,  var  in ds.variables.items():
        if var.long_name == 'LOCAL_CDI_ID':
            break
    else:
        raise ValueError('no variable found with long_name  LOCAL_CDI_ID')
    logger.info('variable  cdi-id {}'.format(var_name))
    cdi_ids = netCDF4.chartostring(ds.variables[var_name][:])
    return cdi_ids

@blueprint.route('/api/get_timeseries', methods=['GET', 'POST'])
@cross_origin()
def get_timeseries():
    """Return timeseries for point data"""

    cdi_id = request.values.get("cdi_id")
    cdi_id = str(cdi_id)

    dataset = request.values.get("dataset")

    ds = get_ds(dataset=dataset)
    if ds is None:
        return jsonify({
            'error': 'data not loaded'
        })

    cdi_ids = get_cdi_ids()

    # get the first
    idx = np.argmax(cdi_ids == cdi_id)

    var_names = [
        name
        for name, var
        in ds.variables.items()
        if name.startswith('var') and not '_' in name
    ]

    # add the variables to the list
    variables = {}
    for var_name in var_names:
        var = ds.variables[var_name]
        try:
            variables[var.long_name] = var[idx]
        except IndexError:
            logger.exception("failed to index {} with index {}".format(var,  idx))

    df = pd.DataFrame(data=variables)
    # get rid of missing data
    df = df.dropna(how='all')

    # get metadata
    date_nums = ds.variables['date_time'][idx]
    date_units = ds.variables['date_time'].units
    date = netCDF4.num2date(date_nums, date_units)
    records = json.loads(df.to_json(orient='records'))

    lon = ds.variables['longitude'][idx]
    lat = ds.variables['latitude'][idx]

    meta_var_names = [
        name
        for name, var
        in ds.variables.items()
        if name.startswith('metavar') and not '_' in name
    ]
    meta_vars = {}

    for var_name in meta_var_names:
        var = ds.variables[var_name]
        if var.dtype == 'S1' and len(var.shape) > 1:
            meta_vars[var.long_name] = str(netCDF4.chartostring(var[idx]))
        else:
            meta_vars[var.long_name] = var[idx]

    ds.close()
    meta_vars.update({
            "date": date.isoformat(),
            "cdi_id": cdi_id,
            "lon": lon,
            "lat": lat
    })

    response = {
        "data": records,
        "meta": meta_vars
    }
    return jsonify(response)

@blueprint.route('/api/slice', methods=['GET', 'POST'])
@cross_origin()
def dataset_slice():
    """Return dataset content."""
    # get the dataset from the current app
    year = int(request.values.get('year', datetime.datetime.now().year))
    depth = int(request.values.get('depth', 0))
    """
    read some variables and return an open file handle,
    based on data selection.
    """
    ds = get_ds()
    if ds is None:
        return jsonify({
            'error': 'data not loaded'
        })

    # slicing in time!
    t0 = netCDF4.date2num(
        datetime.datetime(year=year, month=1, day=1),
        ds.variables['date_time'].units
    )
    t1 = netCDF4.date2num(
        datetime.datetime(year=year + 1, month=1, day=1),
        ds.variables['date_time'].units
    )

    # ensure that our array is always masked
    date_time = np.ma.masked_array(
        ds.variables['date_time'][:]
    )
    is_in_date = np.logical_and(
        date_time[:] >= t0,
        date_time[:] < t1
    ).data
    t = np.empty(
        len(date_time[is_in_date]),
        dtype=type(datetime.datetime.now())
    )

    # split nans and notnans makes it much faster
    dtf = np.where(date_time[is_in_date].mask == False)
    dtt = np.where(date_time[is_in_date].mask == True)
    t[dtf] = netCDF4.num2date(
        date_time[is_in_date][dtf],
        ds.variables['date_time'].units
    )
    # do we have any masked values
    if dtt and dtt[0]:
        t[dtt] = netCDF4.num2date(
            date_time[is_in_date][dtt],
            ds.variables['date_time'].units
        )

    # # TODO: slicing through Depth... Hard with this sort of unstructured netcdf.
    # if data['var1'].long_name == "Depth":
    #     depth = None
    # else:
    depth = None

    if 'lat' in ds.variables:
        lat = ds['lat'][is_in_date]
    elif 'latitude' in ds.variables:
        lat = ds['latitude'][is_in_date]
    if 'lon' in ds.variables:
        lon = ds['lon'][is_in_date]
    elif 'longitude' in ds.variables:
        lon = ds['longitude'][is_in_date]


    cdi_id = netCDF4.chartostring(ds.variables['metavar4'][is_in_date])

    coordinates = np.c_[
        antimeridian_cut(lon),
        lat
    ].tolist()


    features = []
    for i, (coordinate, cdi_id_i) in enumerate(zip(coordinates, cdi_id)):
        geometry = geojson.Point(coordinate)
        feature = geojson.Feature(
            id=i,
            geometry=geometry,
            properties={
                "cdi_id": cdi_id_i
            }
        )
        features.append(feature)

    collection = geojson.FeatureCollection(features=features)
    ds.close()
    return jsonify(collection)

@login_manager.user_loader
def load_user(user_id):
    """user management"""
    return User.get(user_id)


def create_app():
    """Create an app."""

    app = Flask(__name__.split('.')[0])
    # TODO: get this from docker secret /run/secret
    app.secret_key = os.urandom(16)
    app.config['PREFERRED_URL_SCHEME'] = 'https'

    # add user sessions
    login_manager.init_app(app)

    # add urls
    app.register_blueprint(blueprint)


    # add CORS to everything under /api/
    CORS(app, resources={r'/api/*': {'origins': '*'}})

    class JsonCustomEncoder(flask.json.JSONEncoder):
        """encode numpy objects"""
        def default(self, obj):
            if isinstance(obj, (np.ndarray, np.number)):
                return obj.tolist()
            elif isinstance(obj, (complex, np.complex)):
                return [obj.real, obj.imag]
            elif isinstance(obj, set):
                return list(obj)
            elif isinstance(obj, bytes):  # pragma: py3
                return obj.decode()
            return json.JSONEncoder.default(self, obj)
    app.json_encoder = JsonCustomEncoder
    return app
