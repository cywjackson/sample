#! /bin/bash -ex
#
# Find the thread (light weight process id)  in the java webapp process that consume the most CPU.
# Capture a thread dump
# Identify the specific thread (extractTD.py)
#
# Known issue:
#       If there is no high-CPU issue, the highest CPU consuming thread does not exist in the thread dump
#       DO NOT run if there is no issue

# return "%CPU ThreadID"
function getCPULwpid() {
    sed ''$2'!d' $1 | awk '{print $9, $1}'
}

now=`date +%s`
java_pid="`pidof -o %PPID -x java`"
java_pid="${1:-$java_pid}"
td_out="/tmp/${java_pid}-${now}.threaddump"
ps_out="/tmp/${java_pid}-${now}.ps.out"

#ps -p ${java_pid} -Lo %cpu,lwp | grep -v "${java_pid}$" | tr -s ' ' | sed -e 's/^[ \t]*//g' | sort -t ' ' -k1gr | head -5 > ${ps_out}
#  use top instead of ps as ps result is agg. overtime
#  sample result from top:
#    78 root      20   0 10.507g 7.897g  36568 R 99.9 53.8   6:58.77 java
#  150 root      20   0 10.507g 7.897g  36568 R 91.4 53.8   9:11.87 java
# 2058 root      20   0 10.507g 7.897g  36568 S 11.4 53.8   0:07.45 java
# 2078 root      20   0 10.507g 7.897g  36568 S  5.7 53.8   0:01.24 java
#   32 root      20   0 10.507g 7.897g  36568 S  0.0 53.8   0:00.00 java
top -bHn1 -p ${java_pid}  | grep java | grep -v "^\ \+${java_pid}" |  head -5 > ${ps_out}

cpu_lwpid_1=(`getCPULwpid ${ps_out} 1`)

# if cpu from lwpid is lower than the given threshold (default 60), then don't do anything
if [[ `printf %.0f "${cpu_lwpid_1[0]}"` -lt ${2:-75} ]]; then
  exit 0
fi

cpu_lwpid_2=(`getCPULwpid ${ps_out} 2`)
#echo "cpu_lwpid_1: ${cpu_lwpid_1}"
#echo "cpu_lwpid_2: ${cpu_lwpid_2}"
#echo "Writing to  ${td_out}"

# assuming jstack is installed inside the docker container? we do run with full oralce jdk in the container
# outputing the full thread dumps to a file such that we could use sumologic later.
/usr/bin/jstack -l ${java_pid}  > ${td_out}

# assuming python is also installed with the container, and the extractTD.py is executable
/extractTD.py ${cpu_lwpid_1[@]} ${cpu_lwpid_2[@]} ${now} < ${td_out}
