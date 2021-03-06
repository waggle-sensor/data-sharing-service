import argparse
import time
import random
import pika
import json
import os
import logging
import waggle.message as message
import re


WAGGLE_NODE_ID = os.environ.get('WAGGLE_NODE_ID', '0000000000000000')
RABBITMQ_HOST = os.environ.get('RABBITMQ_HOST', 'rabbitmq-server')
RABBITMQ_PORT = int(os.environ.get('RABBITMQ_PORT', '5672'))
RABBITMQ_USERNAME = os.environ.get('RABBITMQ_USERNAME', 'service')
RABBITMQ_PASSWORD = os.environ.get('RABBITMQ_PASSWORD', 'service')


def on_validator_callback(ch, method, properties, body):
    logging.debug("processing message")
    try:
        msg = message.load(body)
    except json.JSONDecodeError:
        logging.warning('failed to parse message %s', body)
        ch.basic_ack(method.delivery_tag)
        return
    except KeyError as key:
        logging.warning('message missing key %s', key)
        ch.basic_ack(method.delivery_tag)
        return

    # tag message with plugin and node metadata
    
    # rabbitmq user_id must has format "plugin.name:version"
    match = re.match(r"plugin\.(\S+:\S+)", properties.user_id)
    if match is None:
        logging.warning('invalid message user ID %s', properties.user_id)
        ch.basic_ack(method.delivery_tag)
        return
    msg.meta["plugin"] = match.group(1)
    msg.meta["node"] = WAGGLE_NODE_ID
    body = message.dump(msg)

    scope = method.routing_key

    if scope not in ['node', 'beehive', 'all']:
        logging.warning('invalid message scope %s', scope)
        ch.basic_ack(method.delivery_tag)
        return

    if scope in ['node', 'all']:
        logging.debug('forwarding message type "%s" to local', msg.name)
        ch.basic_publish(
            exchange='data.topic',
            routing_key=msg.name,
            body=body)

    if scope in ['beehive', 'all']:
        logging.debug('forwarding message type "%s" to beehive', msg.name)
        ch.basic_publish(
            exchange='to-beehive',
            routing_key=msg.name,
            body=body)

    ch.basic_ack(method.delivery_tag)
    logging.debug('processed message')


def declare_exchange_with_queue(ch: pika.adapters.blocking_connection.BlockingChannel, name: str):
    ch.exchange_declare(name, exchange_type='fanout', durable=True)
    ch.queue_declare(name, durable=True)
    ch.queue_bind(name, name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='enable verbose logging')
    parser.add_argument('--rabbitmq-host', default=RABBITMQ_HOST, help='rabbitmq host')
    parser.add_argument('--rabbitmq-port', default=RABBITMQ_PORT, type=int, help='rabbitmq port')
    parser.add_argument('--rabbitmq-username', default=RABBITMQ_USERNAME, help='rabbitmq username')
    parser.add_argument('--rabbitmq-password', default=RABBITMQ_PASSWORD, help='rabbitmq password')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s %(message)s',
        datefmt='%Y/%m/%d %H:%M:%S')
    # pika logging is too verbose, so we turn it down.
    logging.getLogger('pika').setLevel(logging.CRITICAL)

    params = pika.ConnectionParameters(
        host=args.rabbitmq_host,
        port=args.rabbitmq_port,
        credentials=pika.PlainCredentials(
            username=args.rabbitmq_username,
            password=args.rabbitmq_password,
        ),
        client_properties={'name': 'data-sharing-service'},
    )

    logging.info('connecting to rabbitmq server at %s:%d as %s.', params.host, params.port, params.credentials.username)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    logging.info('setting up queues and exchanges.')
    channel.exchange_declare('data.topic', exchange_type='topic', durable=True)
    declare_exchange_with_queue(channel, 'to-validator')
    declare_exchange_with_queue(channel, 'to-beehive')
    
    logging.info('starting main process.')
    channel.basic_consume('to-validator', on_validator_callback)
    channel.start_consuming()

if __name__ == '__main__':
    main()
