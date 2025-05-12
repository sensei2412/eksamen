r tc qdisc show dev r-eth1
for w in 3 5 10 15 20 25; do
  python3 application.py -c -f test10MB.bin -i 10.0.1.2 -p 8080 -w $w
  sleep 1
done
