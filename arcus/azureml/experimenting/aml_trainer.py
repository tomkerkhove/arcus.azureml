from arcus.azureml.experimenting import trainer
from azureml.core import Workspace, Dataset, Datastore, Experiment, Run
from azureml.data.datapath import DataPath
from azureml.data.dataset_error_handling import DatasetValidationError, DatasetExecutionError
from azureml.data.dataset_type_definitions import PromoteHeadersBehavior
from datetime import datetime
import sklearn.metrics as metrics
import joblib
import matplotlib.pyplot as plt
import numpy as np
import json
from tqdm import tqdm
from itertools import product, combinations
from logging import Logger
import logging
import sys

class AzureMLTrainer(trainer.Trainer):
    is_connected: bool = False
    __config_file: str = '.azureml/config.json'
    __workspace: Workspace = None
    __experiment: Experiment = None
    __current_experiment_name: str
    __current_run: Run = None
    __logger: Logger = None

    def __init__(self, experiment_name: str, aml_workspace: Workspace):
        '''
        Initializes a new connected Trainer that will persist and log all runs on AzureML workspace
        Args:
            experiment_name (str): The name of the experiment that will be seen on AzureML
            aml_workspace (Workspace): The connected workspace on AzureML
        '''
        self.__workspace = aml_workspace
        self.__current_experiment_name = experiment_name
        self.__logger = logging.getLogger()
        self.__experiment = Experiment(workspace=self.__workspace, name=experiment_name)

    def new_run(self, description: str = None, copy_folder: bool = True, metrics: dict = None) -> Run:
        '''
        This will begin a new interactive run on the existing AzureML Experiment.  When a previous run was still active, it will be completed.
        Args:
            description (str): An optional description that will be added to the run metadata
            copy_folder (bool): Indicates if the output folder should be snapshotted and persisted
            metrics (dict): The metrics that should be logged in the run already
        Returns:
            Run: the AzureML Run object that can be used for further access and custom logic
        '''
        if(self.__current_run is not None):
            self.__current_run.complete()
        if(copy_folder):
            self.__current_run = self.__experiment.start_logging()
        else:
            self.__current_run = self.__experiment.start_logging(snapshot_directory = None)

        if(metrics is not None):
            for k, v in metrics.items():
                self.__current_run.log(k, v)

        if(description is not None):
            self.__current_run.log('Description', description)
        
        return self.__current_run

    def add_tuning_result(self, run_index: int, train_score: float, test_score: float, sample_count: int, durations:np.array, parameters: dict, estimator):
        '''
        This add results of a cross validation fold to the child run in a Grid Search
        Args:
            train_score (float): The given score of the training data
            test_score (float): The given score of the test data
            sample_count (int): The number of samples that were part of a fold
            durations (np.array): The different durations of the Grid Search
            parameters (dict): The parameter combinations that have been tested in this cross validation fold
            estimate (model): The actual fitted estimator / model that was trained in this fold
        '''
        _child_run = self.__current_run.child_run('Gridsearch' + str(run_index))
        self.__current_run.log_row('Trainscore', score = train_score)
        self.__current_run.log_row('Testscore', score = test_score)

        _table = {
            'Testing score': test_score,
            'Training score': train_score
            }

        for k in parameters.keys():
            v = parameters[k]
            if(v is None):
                v = 'None'
            _child_run.log(k, v)
            _table[k] = v
        
        self.__current_run.log_row('Results', '', **_table)
        _child_run.complete()

    def get_best_model(self, metric_name:str, take_highest:bool = True):
        '''
        Tags and returns the best model of the experiment, based on the given metric

        Args:
            metric_name (str): The name of the metric, such as accuracy
            take_highest (bool): In case of accuracy and score, this is typically True.  In case you want to get the model based on the lowest error, you can use False
        Returns:
            Run: the best run, which will be labeled as best run
        '''
        runs = {}
        run_metrics = {}
        for r in tqdm(self.__experiment.get_runs()):
            metrics = r.get_metrics()
            if metric_name in metrics.keys():
                runs[r.id] = r
                run_metrics[r.id] = metrics
        best_run_id = min(run_metrics, key = lambda k: run_metrics[k][metric_name])
        best_run = runs[best_run_id]
        best_run.tag('Best run')
        return best_run

    def get_azureml_experiment(self):
        '''
        Gives access to the AzureML experiment object
        Returns:
            Experiment: the existing experiment
        '''
        return self.__experiment
        
    def _log_metrics(self, metric_name: str, metric_value: float, description:str = None):
        print(metric_name, metric_value) 

        self.__current_run.log(metric_name, metric_value, description=description)

    
    def _complete_run(self):
        self.__current_run.complete()

    def _log_confmatrix(self, confusion_matrix: np.array, class_names: np.array):
        data = {}
        data['schema_type'] = 'confusion_matrix'
        data['schema_version'] = 'v1'
        data['data'] = {}
        data['data']['class_labels'] = class_names.tolist()
        data['data']['matrix'] = confusion_matrix.tolist()
        
        print(confusion_matrix)

        json_data = json.dumps(data)
        self.__current_run.log_confusion_matrix('Confusion matrix', json_data, description='')

    def _save_roc_curve(self, roc_auc: float, roc_plot: plt):
        self._log_metrics('roc_auc', roc_auc)
        self.__current_run.log_image('ROC Curve', plot=plt)