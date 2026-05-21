#!/bin/bash
#PBS -l nodes=1:ppn=16
#PBS -o log.job
#PBS -N job_name
NCPU=`wc -l < $PBS_NODEFILE`
cd $PBS_O_WORKDIR
echo $PBS_O_WORKDIR $NCPU
