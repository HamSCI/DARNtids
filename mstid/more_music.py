import os
import sys
import shutil
import pickle
import datetime
import json

import matplotlib
from matplotlib import pyplot as plt

import numpy as np
import scipy as sp
from scipy import signal
from scipy import stats

import multiprocessing

#from davitpy import pydarn
#from davitpy import utils

import pyDARNmusic
from pyDARNmusic import music

from mstid import mongo_tools
from .general_lib import prepare_output_dirs

class NumpyEncoder(json.JSONEncoder):
    """
    Custom encoder for numpy data types
    From https://github.com/hmallen/numpyencoder
    """
    def default(self, obj):
        if isinstance(obj, (np.int_, np.intc, np.intp, np.int8,
                            np.int16, np.int32, np.int64, np.uint8,
                            np.uint16, np.uint32, np.uint64)):

            return int(obj)

        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        
        elif isinstance(obj, (np.complex_, np.complex64, np.complex128)):
            return {'real': obj.real, 'imag': obj.imag}
        
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
    
        elif isinstance(obj, (np.bool_)):
            return bool(obj)

        elif isinstance(obj, (np.void)): 
            return None

        return json.JSONEncoder.default(self, obj)

class ProcessLevel(str):
    """
    Class to make comparison of processing order easy.
    """
    def __new__(cls,word):
        str_obj = str.__new__(cls,word)

        # Make a list here of the processing steps in order.
        process_levels  = []
        process_levels.append('None')
        process_levels.append('rti')
        process_levels.append('rti_interp')
        process_levels.append('fft')
        process_levels.append('music')

        str_obj.process_levels = process_levels

        if word in process_levels:
            str_obj.rank = process_levels.index(word)
        else:
            str_obj.rank = 0

        return str_obj

    def __eq__(self,other):
        return self.rank == other.rank

    def __ne__(self,other):
        return self.rank != other.rank

    def __lt__(self,other):
        return self.rank < other.rank

    def __gt__(self,other):
        return self.rank > other.rank

    def __le__(self,other):
        return self.rank <= other.rank

    def __ge__(self,other):
        return self.rank >= other.rank

def get_output_path(radar,sTime,eTime,data_path='music_data/music',create=False):
    lst = []
    lst.append(data_path)
    lst.append(radar.lower())
    lst.append('-'.join([sTime.strftime('%Y%m%d.%H%M'),eTime.strftime('%Y%m%d.%H%M')]))
    path = os.path.join(*lst)
    if create:
        try:
            os.makedirs(path)
        except:
            pass
    return path

def get_pickle_name(radar,sTime,eTime,data_path='music_data/music',getPath=False,createPath=False,
        runfile=False,init_param=False):
    fName = ('-'.join([radar.lower(),sTime.strftime('%Y%m%d.%H%M'),eTime.strftime('%Y%m%d.%H%M')]))+'.p'

    if getPath:
        path    = get_output_path(radar,sTime,eTime,data_path=data_path,create=createPath)
        fName   = os.path.join(path,fName)

    if runfile:
        fName = fName[:-1] + 'runfile.p'

    if init_param:
        fName = fName[:-1] + 'init.json'

    return fName

class Runfile(object):
    def __init__(self,radar,sTime,eTime,runParamsDict,data_path='music_data/music'):
        pickle_path     = get_pickle_name(radar,sTime,eTime
                ,getPath=True,createPath=True,data_path=data_path)
        runfile_path    = pickle_path[:-1] + 'runfile.p'
        
        self.runParams = {}
        for key,value in runParamsDict.items():
            self.runParams[key] = value

        self.runParams['runfile_path'] = runfile_path
        
        with open(runfile_path,'wb') as fl:
            pickle.dump(self,fl)
    
        json_path           = pickle_path[:-1] + 'runfile.json'
        json_dict           = dict(self.runParams)
        json_dict['sTime']  = str(json_dict['sTime'])
        json_dict['eTime']  = str(json_dict['eTime'])
        with open(json_path,'w') as fl:
            json.dump(json_dict,fl,indent=4,sort_keys=True,
                    separators=(', ', ': '),ensure_ascii=False,cls=NumpyEncoder)

def generate_initial_param_file(event,data_path='music_data/music',clear_init_params_dir=False,prefix=None):
    """
    Generates a JSON file for an event with information needed to start a MUSIC run.
    """

    if 'data_path' in event:
        data_path = event['data_path']
    
    init_params_dir  = os.path.join('init_params')
    if clear_init_params_dir:
        shutil.rmtree(init_params_dir,ignore_errors=False)

    if not os.path.exists(init_params_dir):
        os.makedirs(init_params_dir)

    radar   = event.get('radar')
    sTime   = event.get('sTime')
    eTime   = event.get('eTime')

    json_fname  = get_pickle_name(radar,sTime,eTime,init_param=True)
    if prefix is not None:
        json_fname = prefix + json_fname
    json_path   = os.path.join(init_params_dir,json_fname)

    date_fmt    = '%Y-%m-%d %H:%M:%S'
    json_dict           = event.copy()
    json_dict['sTime']  = sTime.strftime(date_fmt)
    json_dict['eTime']  = eTime.strftime(date_fmt)
    with open(json_path,'w') as fl:
        json.dump(json_dict,fl,indent=4,sort_keys=True)

    return json_path

def read_init_param_file(json_path):
    with open(json_path,'r') as fl:
        init_params = json.load(fl)

    date_fmt    = '%Y-%m-%d %H:%M:%S'
    keys        = ['sTime','eTime']
    for key in keys:
        old_val = init_params.get(key)
        new_val = datetime.datetime.strptime(old_val,date_fmt)

        init_params[key]    = new_val 

    return init_params

def get_dataObj(radar,sTime,eTime,data_path='music_data/music'):
    pickle_path = get_pickle_name(radar,sTime,eTime,data_path,getPath=True)
    if os.path.exists(pickle_path):
        with open(pickle_path,'rb') as fl:
            dataObj = pickle.load(fl)
    else:
        dataObj = None

    return dataObj

def create_music_obj(radar, sTime, eTime
        ,beam_limits        = None
        ,gate_limits        = None
        ,interp_resolution  = None
        ,filterNumtaps      = None
        ,srcPath            = None
        ,fitacf_dir         = '/sd-data'
        ,fit_sfx            = 'fitacf'
        ,fovModel           = 'GS'
        ,gscat              = 1
        ):
    """
    srcPath:    Path to Saved Pickle Files
    fitacf_dir: Path to fitacf files

    * [**gscat**] (int): Ground scatter flag.
                    0: all backscatter data 
                    1: ground backscatter only
                    2: ionospheric backscatter only
                    3: all backscatter data with a ground backscatter flag.
    * [**fovModel**] (str): Scatter mapping model.
                    'GS': Ground Scatter Mapping Model.  See Bristow et al. [1994]
                    'IS': Standard SuperDARN scatter mapping model.
    """

    # Calculate time limits of data needed to be loaded to make fiter work. ########
    if interp_resolution != None and filterNumtaps != None:
        load_sTime,load_eTime = pyDARNmusic.filterTimes(sTime,eTime,interp_resolution,filterNumtaps)
    else:
        load_sTime,load_eTime = (sTime, eTime)

    # Load in data and create data objects. ########################################
#    myPtr   = pydarn.sdio.radDataOpen(load_sTime,radar,eTime=load_eTime,channel=channel,cp=cp,fileType=fileType,filtered=boxCarFilter)
    if srcPath is None:
#        myPtr   = pydarn.sdio.radDataOpen(load_sTime,radar,eTime=load_eTime,filtered=fitfilter)
        fitacf  = pyDARNmusic.load_fitacf(radar,load_sTime,load_eTime,data_dir=fitacf_dir)
    else:
        with open(srcPath,'rb') as fl:
            myPtr   = pickle.load(fl)

        # Force load_sTime, load_eTime as if simulated data were real data.
        scan_inxs = []
        for beam_obj in myPtr.beam_list[:]:
            if beam_obj.time < load_sTime or beam_obj.time >= load_eTime:
                myPtr.beam_list.remove(beam_obj)
            else:
                scan_inxs.append(beam_obj.prm.scan)

        myPtr.sTime         = load_sTime
        myPtr.eTime         = load_eTime
        myPtr.scan_index    = np.min(scan_inxs)

    dataObj = music.musicArray(fitacf,fovModel=fovModel,gscat=gscat)
    del fitacf

    bad = False # Innocent until proven guilty.
    if hasattr(dataObj,'messages'):
        if 'No data for this time period.' in dataObj.messages:
            bad = True # At this point, proven guilty.

    if not bad:
        gl = None
        if np.size(gate_limits) == 2:
            if gate_limits[0] != None or gate_limits[1] !=None:
                if gate_limits[0] == None:
                    gl0 = min(dataObj.active.fov['gates'])
                else:
                    gl0 = gate_limits[0]
                if gate_limits[1] == None:
                    gl1 = max(dataObj.active.fov['gates'])
                else:
                    gl1 = gate_limits[1]
                gl = (gl0, gl1)

        if gl != None:
            pyDARNmusic.defineLimits(dataObj,gateLimits=gl)

        bl = None
        if np.size(beam_limits) == 2:
            if beam_limits[0] != None or beam_limits[1] !=None:
                if beam_limits[0] == None:
                    bl0 = min(dataObj.active.fov['beams'])
                else:
                    bl0 = beam_limits[0]
                if beam_limits[1] == None:
                    bl1 = max(dataObj.active.fov['beams'])
                else:
                    bl1 = beam_limits[1]
                bl = (bl0, bl1)

        if bl != None:
            pyDARNmusic.defineLimits(dataObj,beamLimits=bl)

        dataObj = pyDARNmusic.checkDataQuality(dataObj,dataSet='originalFit',sTime=sTime,eTime=eTime)
    return dataObj

def auto_range(radar,sTime,eTime,dataObj,bad_range_km=500,
        figsize = (20,7),output_dir='output',plot=False):
    """
    Automatically determine the range gates used in analysis.

    bad_range_km: The minimum acceptable range away from the radar
    (after the range mapping has been applied). 500 km for ground scatter
    gets you past FOV distortion.
    """

    # Auto-ranging code ############################################################
    currentData = dataObj.DS000_originalFit
    timeInx = np.where(np.logical_and(currentData.time >= sTime,currentData.time <= eTime))[0]

    bins    = currentData.fov['gates']
    # Integrate over time and beams to give a distribution as a funtion of range
    dist    = np.nansum(np.nansum(currentData.data[timeInx,:,:],axis=0),axis=0)

    # Set max val of the distribution and convert all NaNs to 0.
    dist    = np.nan_to_num(dist / np.nanmax(dist))

    nrPts   = 1000
    distArr = np.array([],dtype=int)
    for rg in range(len(bins)):
        gate    = bins[rg]
        nrGate  = int(np.floor(dist[rg]*nrPts))
        if nrGate < 0:
            nrGate = 0

        distArr = np.concatenate([distArr,np.ones(nrGate,dtype=int)*gate])

    hist,bins           = np.histogram(distArr,bins=bins,density=True)
    hist                = sp.signal.medfilt(hist,kernel_size=11)

    arg_max = np.argmax(hist)

    max_val = hist[arg_max]
    thresh  = 0.18

    good    = [arg_max]
    #Search connected lower
    search_inx  = np.where(bins[:-1] < arg_max)[0]
    search_inx.sort()
    search_inx  = search_inx[::-1]
    for inx in search_inx:
        if hist[inx] > thresh*max_val:
            good.append(inx)
        else:
            break

    #Search connected upper
    search_inx  = np.where(bins[:-1] > arg_max)[0]
    search_inx.sort()
    for inx in search_inx:
        if hist[inx] > thresh*max_val:
            good.append(inx)
        else:
            break

    good.sort() 

    min_range   = min(good)
    max_range   = max(good)

    #Check for and correct bad start gate (due to GS mapping algorithm)
    if bad_range_km is not None:
        bad_range   = np.max(np.where(dataObj.DS000_originalFit.fov['slantRCenter'] < bad_range_km)[1])
        if min_range <= bad_range: min_range = bad_range+1

    dataObj.DS000_originalFit.metadata['gateLimits'] = (min_range,max_range)

    if plot:
        # Make some plots. #############################################################
        if not os.path.exists(output_dir): os.makedirs(output_dir)

        file_name   = '.'.join([radar,sTime.strftime('%Y%m%d.%H%M'),eTime.strftime('%Y%m%d.%H%M'),'rangeDist','png'])

        font = {'weight':'normal','size':12}
        matplotlib.rc('font',**font)
        fig     = plt.figure(figsize=figsize)
    #    axis    = fig.add_subplot(121)
        axis    = fig.add_subplot(221)

        axis.bar(bins[:-1],hist)
        axis.bar(bins[good],hist[good],color='r')

    #    hist,bins,patches   = axis.hist(distArr,bins=bins,normed=1)
    #    for xx in xrange(fitted.n_components):
    #        mu      = fitted.means_[xx]
    #        sigma   = np.sqrt(fitted.covars_[xx])
    #        y       = stats.norm.pdf(bins,mu,sigma)
    #        axis.plot(bins,y)

        axis.set_xlabel('Range Gate')
        axis.set_ylabel('Normalized Weight')
        axis.set_title(file_name)

        axis    = fig.add_subplot(223)
        axis.plot(bins[:-1],np.cumsum(hist))
        axis.set_xlabel('Range Gate')
        axis.set_ylabel('Power CDF')

        axis    = fig.add_subplot(122)
        pyDARNmusic.plotting.rtp.musicRTP3(dataObj
            , dataSet='originalFit'
    #        , beams=beam
            , xlim=None
            , ylim=None
            , coords='gate'
            , axis=axis
            , plotZeros=True
            , xBoundaryLimits=(sTime,eTime)
    #        , axvlines = axvlines
    #        , autoScale=autoScale
            )
       ################################################################################ 
        fig.tight_layout(w_pad=5.0)
        fig.savefig(os.path.join(output_dir,file_name))
        plt.close(fig)

    return (min_range,max_range)

def zeropad_data(dataObj):
    time_delt   = np.max(dataObj.active.time) - np.min(dataObj.active.time)
    samp_per    = datetime.timedelta(seconds=dataObj.active.samplePeriod())
    new_time = np.array(
    (dataObj.active.time - time_delt - samp_per).tolist() + \
    (dataObj.active.time).tolist() + \
    (dataObj.active.time + time_delt + samp_per).tolist()
    )

    size     = dataObj.active.data.shape[0]
    new_data = np.pad(dataObj.active.data,((size,size),(0,0),(0,0)),'constant')

    new_sig      = dataObj.active.copy('zeropad','Zero Padded Signal')
    new_sig.time = new_time
    new_sig.data = new_data

    new_sig.setMetadata(sTime=new_time.min())
    new_sig.setMetadata(eTime=new_time.max())

    new_sig.setActive()

def window_beam_gate(dataObj,dataSet='active',window='hann'):
    
    currentData = pyDARNmusic.getDataSet(dataObj,dataSet)
    currentData = currentData.applyLimits()

    nrTimes, nrBeams, nrGates = np.shape(currentData.data)

    win = sp.signal.get_window(window,nrGates,fftbins=False)
    win.shape = (1,1,nrGates)

    new_sig      = dataObj.active.copy('windowed_gate','Windowed Gate Dimension')
    new_sig.data = win*dataObj.active.data
    new_sig.setActive()
    
    win = sp.signal.get_window(window,nrBeams,fftbins=False)
    win.shape = (1,nrBeams,1)

    new_sig      = dataObj.active.copy('windowed_beam','Windowed Beam Dimension')
    new_sig.data = win*dataObj.active.data
    new_sig.setActive()

def run_music_init_param_file(filename):
    init_params = read_init_param_file(filename)
    run_music(**init_params)

def mark_process_level(level,radar,sTime,eTime,data_path='music_data/music',
    filename='processing_level_completed.txt',**kwargs):

    music_path  = get_output_path(radar,sTime,eTime,data_path=data_path)
    filepath    = os.path.join(music_path,filename)

    with open(filepath,'w') as fl:
        fl.write(level)
    return

def get_process_level(radar,sTime,eTime,data_path='music_data/music',
    filename='processing_level_completed.txt',**kwargs):

    music_path  = get_output_path(radar,sTime,eTime,data_path=data_path)
    filepath    = os.path.join(music_path,filename)

    if not os.path.exists(filepath):
        return ProcessLevel(None)

    with open(filepath,'r') as fl:
        completed_process_level = fl.readline()

    return ProcessLevel(completed_process_level)

def run_music(radar,sTime,eTime,
    process_level           = 'music',
    make_plots              = True,
    data_path               = 'music_data/music',
    fovModel                = 'GS',
    gscat                   = 1,
    boxcar_filter           = True,
    auto_range_on           = True,
    bad_range_km            = None,
    beam_limits             = (None, None),
    gate_limits             = (0,80),
    interp_resolution       = 60.,
    filter_numtaps          = 101.,
    filter_cutoff_low       = 0.0003,
    filter_cutoff_high      = 0.0012,
    detrend                 = True,
    hanning_window_space    = True,
    hanning_window_time     = True,
    zeropad                 = True,
    kx_max                  = 0.05,
    ky_max                  = 0.05,
    autodetect_threshold    = 0.35,
    neighborhood            = (10,10),
    mstid_list              = None,
    db_name                 = 'mstid',
    mongo_port              = 27017,
    srcPath                 = None,
    fitacf_dir              = '/sd-data',
    **kwargs):

    """
    bad_range_km: Reject ranges less than this in GS Mapped Range
        For MSTID Index Calculation, set to None.
        For MUSIC Calculation, set to 500 km to get past FOV distortion.
    """
    
    print(datetime.datetime.now(), 'Processing: ', radar, sTime)

    process_level   = ProcessLevel(str(process_level))
    music_path  = get_output_path(radar, sTime, eTime,data_path=data_path,create=True)
    pickle_path = get_pickle_name(radar,sTime,eTime,data_path=data_path,getPath=True)

    prepare_output_dirs({0:music_path},clear_output_dirs=True)

    good            = True
    reject_messages = []
#    try:
    if True:
        dataObj = create_music_obj(radar.lower(), sTime, eTime
            ,beam_limits                = beam_limits
            ,gate_limits                = gate_limits
            ,interp_resolution          = interp_resolution
            ,filterNumtaps              = filter_numtaps 
            ,srcPath                    = srcPath
            ,fovModel                   = fovModel
            ,gscat                      = gscat
            ,fitacf_dir                 = fitacf_dir
            )
#    except:
#        dataObj = None
#        reject_messages.append('Unspecified data loading error. Radar probably running a non-standard mode that this code is not equipped to handle.')
#        good    = False

    # Basic Data Quality Check #####################################################
    if good:
        if hasattr(dataObj,'messages'):
            messages            = '\n'.join([music_path]+dataObj.messages)
            messages_filename   = os.path.join(music_path,'messages.txt')
            with open(messages_filename,'w') as fl:
                fl.write(messages)
            print(messages)
            error_text = []
            error_text.append('No data for this time period.')
            for txt in error_text:
                if txt in dataObj.messages:
                    reject_messages.append(txt)
                    good = False

    if good:
        if hasattr(dataObj,'active'):
            if not dataObj.active.metadata['good_period']:
                reject_messages.append('Bad data period as determined by checkDataQuality().')
                good = False
    
    # Make sure FOV object and data array have the same number of rangegates.
    if good:
        if dataObj.active.fov['beams'].size != dataObj.active.data.shape[1]:
            reject_messages.append('Number of FOV beams != number of beams in data array. Rejecting observation window.')
            good = False

    if good:
        if dataObj.active.fov['gates'].size != dataObj.active.data.shape[2]:
            reject_messages.append('Number of FOV gates != number of gates in data array.  Radar probably running a non-standard mode that this code is not equipped to handle.')
            good = False

    if boxcar_filter and good:
        pyDARNmusic.boxcarFilter(dataObj)

    # Determine auto-range if called for. ########################################## 
    if auto_range_on and good:
        try:
            gate_limits = auto_range(radar,sTime,eTime,dataObj,bad_range_km=bad_range_km)
            pyDARNmusic.defineLimits(dataObj,gateLimits=gate_limits)
        except:
            import ipdb; ipdb.set_trace()
            reject_messages.append('auto_range() computation error.')
            good = False

        if (gate_limits[1] - gate_limits[0]) <= 5:
            reject_messages.append('auto_range() too small.')
            good = False

#        try:
#            gate_limits = auto_range(radar,sTime,eTime,dataObj,bad_range_km=bad_range_km)
#            pyDARNmusic.defineLimits(dataObj,gateLimits=gate_limits)
#
#            if (gate_limits[1] - gate_limits[0]) <= 5:
#                reject_messages.append('auto_range() too small.')
#                good = False
#        except:
#            reject_messages.append('auto_range() failed! There may not be enough good gates in this period.')
#            good = False


    # Create a run file. ###########################################################
    run_params = {}
    run_params['radar']                 = radar.lower()
    run_params['sTime']                 = sTime
    run_params['eTime']                 = eTime
    run_params['beam_limits']           = beam_limits
    run_params['gate_limits']           = gate_limits
    run_params['interp_resolution']     = interp_resolution
    run_params['filter_numtaps']        = filter_numtaps
    run_params['filter_cutoff_low']     = filter_cutoff_low
    run_params['filter_cutoff_high']    = filter_cutoff_high
    run_params['music_path']            = music_path 
    run_params['data_path']             = data_path 
    run_params['pickle_path']           = pickle_path
    run_params['detrend']               = detrend 
    run_params['hanning_window_space']  = hanning_window_space
    run_params['hanning_window_time']   = hanning_window_time
    run_params['zeropad']               = zeropad 
    run_params['kx_max']                = kx_max
    run_params['ky_max']                = ky_max
    run_params['autodetect_threshold']  = autodetect_threshold
    run_params['neighborhood']          = neighborhood
    runfile = Runfile(radar.lower(), sTime, eTime, run_params,data_path=data_path)

    completed_process_level = 'rti'

    # If basic data quality check fails, save what we have and return. ############# 
    if not good:
        print('\n'.join(reject_messages))
        if db_name is not None:
            mongo_tools.dataObj_update_mongoDb(radar,sTime,eTime,dataObj,
                    mstid_list,db_name,mongo_port)
        with open(pickle_path,'wb') as fl:
            pickle.dump(dataObj,fl)
        # Mark processing at MUSIC level to prevent trying to process again.
        mark_process_level('music',**run_params)
        return

    # Now do the processing. #######################################################
    if process_level >= ProcessLevel('rti_interp'):
        dataObj.active.applyLimits()

        pyDARNmusic.beamInterpolation(dataObj,dataSet='limitsApplied')
        pyDARNmusic.determineRelativePosition(dataObj)

        pyDARNmusic.timeInterpolation(dataObj,timeRes=interp_resolution)
        pyDARNmusic.nan_to_num(dataObj)

        calculate_terminator_for_dataSet(dataObj)

        completed_process_level = 'rti_interp'

    if process_level >= ProcessLevel('fft'):
        if not filter_numtaps is None:
            filt = music.filter(dataObj, dataSet='active', numtaps=filter_numtaps, cutoff_low=filter_cutoff_low, cutoff_high=filter_cutoff_high)

            if make_plots:
                figsize    = (20,10)
                plotSerial = 999
                fig = plt.figure(figsize=figsize)
                filt.plotImpulseResponse(fig=fig)
                fileName = os.path.join(music_path,'%03i_impulseResponse.png' % plotSerial)
                fig.savefig(fileName,bbox_inches='tight')
                plt.close(fig)

                fig = plt.figure(figsize=figsize)
                filt.plotTransferFunction(fig=fig,xmax=0.004)
                fileName = os.path.join(music_path,'%03i_transferFunction.png' % plotSerial)
                fig.savefig(fileName,bbox_inches='tight')
                plt.close(fig)

        if detrend:
            pyDARNmusic.detrend(dataObj, dataSet='active')

        # Recalculate terminator because time vector changed.
        calculate_terminator_for_dataSet(dataObj)

        if hanning_window_time:
            pyDARNmusic.windowData(dataObj, dataSet='active')

        if hanning_window_space:
            window_beam_gate(dataObj)

        if zeropad:
            zeropad_data(dataObj)

        # Recalculate terminator because time vector changed.
        calculate_terminator_for_dataSet(dataObj)
        pyDARNmusic.calculateFFT(dataObj)

        completed_process_level = 'fft'

    if process_level >= ProcessLevel('music'):
        pyDARNmusic.calculateDlm(dataObj)
        pyDARNmusic.calculateKarr(dataObj,kxMax=kx_max,kyMax=ky_max)
        pyDARNmusic.detectSignals(dataObj,threshold=autodetect_threshold,neighborhood=neighborhood)
        sigs_to_txt(dataObj,music_path)
        completed_process_level = 'music'

    # Save the data file. ##########################################################  
    with open(pickle_path,'wb') as fl:
        pickle.dump(dataObj,fl)

    mark_process_level(completed_process_level,**run_params)

    # Update mongoDb. ############################################################## 
    if db_name is not None:
        mongo_tools.dataObj_update_mongoDb(radar,sTime,eTime,dataObj,
                mstid_list,db_name,mongo_port)

    # Run MUSIC and Plotting Code ##################################################
    if make_plots:
        music_plot_all(run_params,dataObj,process_level=process_level)

def music_plot_all(run_params,dataObj,process_level='music'):
    output_dir  = run_params['music_path']
    sTime       = run_params['sTime']
    eTime       = run_params['eTime']
    time        = sTime + (eTime - sTime)/2

    process_level   = ProcessLevel(str(process_level))

    figsize     = (20,10)
    plotSerial  = 0

    rti_xlim    = get_default_rti_times(run_params,dataObj)
    rti_ylim    = get_default_gate_range(run_params,dataObj)
    rti_beams   = get_default_beams(run_params,dataObj)

    dataObj.DS000_originalFit.metadata['timeLimits'] = [sTime,eTime]
    fileName = os.path.join(output_dir,'%03i_originalFit_RTI.png' % plotSerial)
    plot_music_rti(dataObj,
            fileName    = fileName,
            dataSet     = "originalFit",
            beam        = rti_beams,
            xlim        = rti_xlim,
            ylim        = rti_ylim)

    dataObj.DS000_originalFit.metadata.pop('timeLimits',None)
    plotSerial = plotSerial + 1

    if process_level == ProcessLevel('rti'):
        return

    if 'good_period' in dataObj.active.metadata:
        if not dataObj.active.metadata['good_period']:
            return

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.musicFan(dataObj,plotZeros=True,dataSet='originalFit',time=time,fig=fig,subplot_tuple=(1,2,1))
    pyDARNmusic.plotting.musicPlot.musicFan(dataObj,plotZeros=True,dataSet='beamInterpolated',time=time,fig=fig,subplot_tuple=(1,2,2))
    fileName = os.path.join(output_dir,'%03i_beamInterp_fan.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.plotRelativeRanges(dataObj,time=time,fig=fig,dataSet='beamInterpolated')
    fileName = os.path.join(output_dir,'%03i_ranges.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.timeSeriesMultiPlot(dataObj,dataSet="DS002_beamInterpolated",dataSet2='DS001_limitsApplied',fig=fig)
    fileName = os.path.join(output_dir,'%03i_beamInterp.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.timeSeriesMultiPlot(dataObj,dataSet='timeInterpolated',dataSet2='beamInterpolated',fig=fig)
    fileName = os.path.join(output_dir,'%03i_timeInterp.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    if process_level == ProcessLevel('rti_interp'):
        return

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.timeSeriesMultiPlot(dataObj,fig=fig,dataSet="DS005_filtered")
    fileName = os.path.join(output_dir,'%03i_filtered.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.timeSeriesMultiPlot(dataObj,fig=fig)
    fileName = os.path.join(output_dir,'%03i_detrendedData.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    if run_params.get('window_data'):
        fig = plt.figure(figsize=figsize)
        pyDARNmusic.plotting.musicPlot.timeSeriesMultiPlot(dataObj,fig=fig)
        fileName = os.path.join(output_dir,'%03i_windowedData.png' % plotSerial)
        fig.savefig(fileName,bbox_inches='tight')
        plt.close(fig)
        plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.spectrumMultiPlot(dataObj,fig=fig,xlim=(-0.0025,0.0025))
    fileName = os.path.join(output_dir,'%03i_spectrum.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.spectrumMultiPlot(dataObj,fig=fig,plotType='magnitude',xlim=(0,0.0025))
    fileName = os.path.join(output_dir,'%03i_magnitude.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.spectrumMultiPlot(dataObj,fig=fig,plotType='phase',xlim=(0,0.0025))
    fileName = os.path.join(output_dir,'%03i_phase.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.musicFan(dataObj,plotZeros=True,autoScale=True,time=time,fig=fig,subplot_tuple=(1,1,1))
    fileName = os.path.join(output_dir,'%03i_finalDataFan.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    ax  = fig.add_subplot(111)
    pyDARNmusic.plotting.rtp.musicRTP(dataObj,plotZeros=True,axis=ax,autoScale=True)
    fileName = os.path.join(output_dir,'%03i_finalDataRTI.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.plotFullSpectrum(dataObj,fig=fig,xlim=(0,0.0015))
    fileName = os.path.join(output_dir,'%03i_fullSpectrum.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    if process_level == ProcessLevel('fft'):
        return

    fig = plt.figure(figsize=figsize)
    pyDARNmusic.plotting.musicPlot.plotDlm(dataObj,fig=fig)
    fileName = os.path.join(output_dir,'%03i_dlm_abs.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

    fig = plt.figure(figsize=(10,10))
    pyDARNmusic.plotting.musicPlot.plotKarr(dataObj,fig=fig,maxSignals=25,cmap='viridis')
    fileName = os.path.join(output_dir,'%03i_karr.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1
    
    fig = plt.figure(figsize=(10,10))
    pyDARNmusic.plotting.musicPlot.plotKarrDetected(dataObj,fig=fig)
    fileName = os.path.join(output_dir,'%03i_karrDetected.png' % plotSerial)
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)
    plotSerial = plotSerial + 1

def plot_music_rti(dataObj
        , dataSet='active'
        , beam=[4,7,13]
        , xlim=None
        , ylim=None
        , coords='gate'
        , fileName='rti.png'
        , scale=None
        , plotZeros=False
        , xBoundaryLimits=None
        , yBoundaryLimits=None
        , autoScale=False
        , axvlines = None
        , figsize = (20,15)
        ):

    fig     = plt.figure(figsize=figsize)
    axis    = fig.add_subplot(111)

    pyDARNmusic.plotting.rtp.musicRTP3(dataObj
        , dataSet=dataSet
        , beams=beam
        , xlim=xlim
        , ylim=ylim
        , coords=coords
        , axis=axis
        , scale=scale
        , plotZeros=plotZeros
        , xBoundaryLimits=xBoundaryLimits
        , yBoundaryLimits=yBoundaryLimits
        , axvlines = axvlines
        , autoScale=autoScale
        )
    fig.savefig(fileName,bbox_inches='tight')
    plt.close(fig)

def get_default_rti_times(musicParams,dataObj=None,min_hours=4):
    #Set up suggested boundaries for RTI replotting.
    min_timedelta = datetime.timedelta(hours=min_hours)
    duration = musicParams['eTime'] - musicParams['sTime']
    if duration < min_timedelta:
        center_time = musicParams['sTime'] + duration/2
        min_time = center_time - min_timedelta/2
        max_time = center_time + min_timedelta/2
    else:
        min_time = musicParams['sTime']
        max_time = musicParams['eTime']

    return min_time,max_time


def get_default_gate_range(musicParams,dataObj=None,gate_buffer=10):
    min_gate = None
    max_gate = None

    if hasattr(dataObj,'messages'):
        if 'No data for this time period.' in dataObj.messages:
            dataObj = None

    if 'gate_limits' in musicParams:
        if musicParams['gate_limits'] is not None:
            if musicParams['gate_limits'][0] is not None:
                min_gate = musicParams['gate_limits'][0] - gate_buffer
                if dataObj is not None:
                    gts = dataObj.DS000_originalFit.fov['gates']
                    if min_gate < min(gts): min_gate = min(gts)
            if musicParams['gate_limits'][1] is not None:
                max_gate = musicParams['gate_limits'][1] + gate_buffer
                if dataObj is not None:
                    gts = dataObj.DS000_originalFit.fov['gates']
                    if max_gate > max(gts): max_gate = max(gts)

    return min_gate,max_gate

def get_default_beams(musicParams,dataObj=None,beams=[4,7,13]):
    if hasattr(dataObj,'messages'):
        if 'No data for this time period.' in dataObj.messages:
            dataObj = None

    if dataObj is not None:
        new_beam_list = []
        bms = dataObj.DS000_originalFit.fov['beams']
        for beam in beams:
            if beam in bms:
                new_beam_list.append(beam)
            else:
                new_beam_list.append(bms[0])
    else:
        new_beam_list = beams
    return new_beam_list

def calculate_terminator(lats,lons,dates):
    lats    = np.array(lats)
    lons    = np.array(lons)
    dates   = np.array(dates)

    if lats.shape == (): lats.shape = (1,)
    if lons.shape == (): lons.shape = (1,)

    shape       = (len(dates),lats.shape[0],lats.shape[1])

    term_lats   = np.zeros(shape,dtype=float)
    term_tau    = np.zeros(shape,dtype=float)
    term_dec    = np.zeros(shape,dtype=float)

    terminator  = np.ones(shape,dtype=np.bool)

    for inx,date in enumerate(dates):
        term_tup = pyDARNmusic.utils.timeUtils.daynight_terminator(date, lons)
        term_lats[inx,:,:]  = term_tup[0]
        term_tau[inx,:,:]   = term_tup[1]
        term_dec[inx,:,:]   = term_tup[2]

    nh_summer = term_dec > 0
    nh_winter = term_dec < 0

    tmp         = lats[:]
    tmp.shape   = (1,tmp.shape[0],tmp.shape[1])
    lats_arr    = np.repeat(tmp,len(dates),axis=0)
    terminator[nh_summer] = lats_arr[nh_summer] < term_lats[nh_summer]
    terminator[nh_winter] = lats_arr[nh_winter] > term_lats[nh_winter]

    return terminator

def calculate_terminator_for_dataSet(dataObj,dataSet='active'):
    currentData = pyDARNmusic.getDataSet(dataObj,dataSet)

    term_ctr    = calculate_terminator(currentData.fov['latCenter'],currentData.fov['lonCenter'],currentData.time)
    currentData.terminator = term_ctr

#    term_full    = calculate_terminator(currentData.fov.latFull,currentData.fov.lonFull,currentData.time)
#    currentData.fov.terminatorFull = term_full
    return dataObj

def sigs_to_txt(dataObj,music_path,data_set='active'):
    currentData = getattr(dataObj,data_set,None)
    if hasattr(currentData,'sigDetect'):
        sigs    = currentData.sigDetect
        sigs.reorder()

        sigList     = []
        serialNr    = 0
        for sig in sigs.info:
            sigInfo = {}
            sigInfo['order']    = int(sig['order'])
            sigInfo['kx']       = float(sig['kx'])
            sigInfo['ky']       = float(sig['ky'])
            sigInfo['k']        = float(sig['k'])
            sigInfo['lambda']   = float(sig['lambda'])
            sigInfo['azm']      = float(sig['azm'])
            sigInfo['freq']     = float(sig['freq'])
            sigInfo['period']   = float(sig['period'])
            sigInfo['vel']      = float(sig['vel'])
            sigInfo['max']      = float(sig['max'])
            sigInfo['area']     = float(sig['area'])
            sigInfo['serialNr'] = serialNr
            sigList.append(sigInfo)
            serialNr = serialNr + 1

        txtPath = os.path.join(music_path,'karr.txt')
        with open(txtPath,'w') as fl:
            txt = '{:<5}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}\n'.format('Number','Kx','Ky','|K|','lambda','Azm','f','T','v','Value','Area')
            fl.write(txt)
            txt = '{:<5}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}\n'.format('','[1/km]','[1/km]','[1/km]','[km]','[deg]','[mHz]','[min]','[m/s]','','[px]')
            fl.write(txt)

            for sigInfo in sigList:
                txt = '{:<5}{:>10.3f}{:>10.3f}{:>10.3f}{:>10.0f}{:>10.0f}{:>10.3f}{:>10.0f}{:>10.0f}{:>10.3f}{:>10.0f}\n'.format(
                     sigInfo['order']
                    ,sigInfo['kx']
                    ,sigInfo['ky']
                    ,sigInfo['k']
                    ,sigInfo['lambda']
                    ,sigInfo['azm']
                    ,sigInfo['freq'] * 1000.
                    ,sigInfo['period']/60.
                    ,sigInfo['vel']
                    ,sigInfo['max']
                    ,sigInfo['area'])
                fl.write(txt)

def get_orig_rti_info(dataObj,sTime,eTime):
    """
    Determine basic statistical information about raw radar data and return
    it in a dictionary.

    These parameters are computed on the original data (dataObj.DS000_originalFit)
    between sTime and eTime (that you specify), and within the dataObj.active beam
    and range gates.
    
    The following paramters are computed and returned in a dictionary:
        orig_rti_cnt:       Total number of ground scatter points measured by the radar.
        orig_rti_possible:  Largest possible number for orig_rti_cnt
                            (nr_beams*nr_gates*nr_time)
        orig_rti_fraction:  orig_rti_cnt / orig_rti_possible
        orig_rti_mean:      nanmean(orig_fit)
        orig_rti_median:    nanmedian(orig_fit)
        orig_rti_std:       nanstd(orig_fit)
    """
    currentData = dataObj.DS000_originalFit

    # Store summary RTI info into db.
    #Get information from original RTI plots.
    #Get boundary info...
    gates       = dataObj.DS000_originalFit.fov['gates']
    beams       = dataObj.DS000_originalFit.fov['beams']

    beam_min    = dataObj.active.fov['beams'].min()
    beam_max    = dataObj.active.fov['beams'].max()
    gate_min    = dataObj.active.fov['gates'].min()
    gate_max    = dataObj.active.fov['gates'].max()

    time_mask   = np.logical_and(dataObj.DS000_originalFit.time >= sTime, dataObj.DS000_originalFit.time < eTime)
    beam_mask   = np.logical_and(beams >= beam_min, beams <= beam_max)
    gate_mask   = np.logical_and(gates >= gate_min, gates <= gate_max)

    time_inx    = np.where(time_mask)[0]
    beam_inx    = np.where(beam_mask)[0]
    gate_inx    = np.where(gate_mask)[0]

    inx_arr     = np.meshgrid(time_inx,beam_inx,gate_inx)
    
    time_arr    = inx_arr[0]
    beam_arr    = inx_arr[1]
    gate_arr    = inx_arr[2]

    orig_fit    = dataObj.DS000_originalFit.data[time_arr,beam_arr,gate_arr]

    dct = {}
    dct['orig_rti_cnt']         = float( np.sum(np.isfinite(orig_fit)))
    dct['orig_rti_possible']    = float(orig_fit.size)
    if dct['orig_rti_possible'] != 0.:
        dct['orig_rti_fraction']    = dct['orig_rti_cnt'] / dct['orig_rti_possible']
    else:
        dct['orig_rti_fraction']    = 0.

    dct['orig_rti_mean']        = float(np.nanmean(orig_fit,axis=None))
    dct['orig_rti_median']      = float(np.nanmedian(orig_fit,axis=None))
    dct['orig_rti_std']         = float(np.nanstd(orig_fit,axis=None))
    return dct


