# coding=utf-8
import datetime
import os
import random
import string
import time
import unittest
from collections import namedtuple
from decimal import Decimal
from keyword import kwlist
from xml.etree.ElementTree import ParseError

import requests
from six import PY2, text_type, string_types, python_2_unicode_compatible
from yaml import load

from exchangelib import close_connections
from exchangelib.account import Account, SAVE_ONLY, SEND_ONLY, SEND_AND_SAVE_COPY
from exchangelib.attachments import Attachment, FileAttachment, ItemAttachment
from exchangelib.autodiscover import AutodiscoverProtocol, discover
from exchangelib.configuration import Configuration
from exchangelib.credentials import DELEGATE, IMPERSONATION, Credentials, ServiceAccount
from exchangelib.errors import RelativeRedirect, ErrorItemNotFound, ErrorInvalidOperation, AutoDiscoverRedirect, \
    AutoDiscoverCircularRedirect, AutoDiscoverFailed, ErrorNonExistentMailbox, UnknownTimeZone, \
    ErrorNameResolutionNoResults, TransportError, RedirectError, CASError, RateLimitError, UnauthorizedError, \
    ErrorInvalidChangeKey, ErrorInvalidIdMalformed, ErrorContainsFilterWrongType, ErrorAccessDenied, \
    ErrorFolderNotFound, SOAPError
from exchangelib.ewsdatetime import EWSDateTime, EWSDate, EWSTimeZone, UTC, UTC_NOW
from exchangelib.extended_properties import ExtendedProperty
from exchangelib.fields import BooleanField, IntegerField, DecimalField, TextField, EmailField, URIField, ChoiceField, \
    BodyField, DateTimeField, Base64Field, EWSElementField, IndexedField, PhoneNumberField, EmailAddressField, \
    PhysicalAddressField, ExtendedPropertyField, MailboxField, AttendeesField, AttachmentField
from exchangelib.folders import Calendar, DeletedItems, Drafts, Inbox, Outbox, SentItems, JunkEmail, Messages, Tasks, \
    Contacts, Folder
from exchangelib.indexed_properties import IndexedElement, EmailAddress, PhysicalAddress, PhoneNumber
from exchangelib.items import Item, CalendarItem, Message, Contact, Task, ALL_OCCURRENCIES
from exchangelib.properties import Attendee, Mailbox, Choice, RoomList, MessageHeader, Room, EWSElement
from exchangelib.queryset import QuerySet, DoesNotExist, MultipleObjectsReturned
from exchangelib.restriction import Restriction, Q
from exchangelib.services import GetServerTimeZones, GetRoomLists, GetRooms, GetAttachment, ResolveNames, TNS
from exchangelib.transport import NTLM, wrap
from exchangelib.util import chunkify, peek, get_redirect_url, to_xml, BOM, get_domain, \
    post_ratelimited, create_element, CONNECTION_ERRORS
from exchangelib.version import Build, Version
from exchangelib.winzone import generate_map, PYTZ_TO_MS_TIMEZONE_MAP

if PY2:
    FileNotFoundError = OSError

string_type = string_types[0]


class BuildTest(unittest.TestCase):
    def test_magic(self):
        with self.assertRaises(ValueError):
            Build(7, 0)
        self.assertEqual(str(Build(9, 8, 7, 6)), '9.8.7.6')

    def test_compare(self):
        self.assertEqual(Build(15, 0, 1, 2), Build(15, 0, 1, 2))
        self.assertLess(Build(15, 0, 1, 2), Build(15, 0, 1, 3))
        self.assertLess(Build(15, 0, 1, 2), Build(15, 0, 2, 2))
        self.assertLess(Build(15, 0, 1, 2), Build(15, 1, 1, 2))
        self.assertLess(Build(15, 0, 1, 2), Build(16, 0, 1, 2))
        self.assertLessEqual(Build(15, 0, 1, 2), Build(15, 0, 1, 2))
        self.assertGreater(Build(15, 0, 1, 2), Build(15, 0, 1, 1))
        self.assertGreater(Build(15, 0, 1, 2), Build(15, 0, 0, 2))
        self.assertGreater(Build(15, 1, 1, 2), Build(15, 0, 1, 2))
        self.assertGreater(Build(15, 0, 1, 2), Build(14, 0, 1, 2))
        self.assertGreaterEqual(Build(15, 0, 1, 2), Build(15, 0, 1, 2))

    def test_api_version(self):
        self.assertEqual(Build(8, 0).api_version(), 'Exchange2007')
        self.assertEqual(Build(8, 1).api_version(), 'Exchange2007_SP1')
        self.assertEqual(Build(8, 2).api_version(), 'Exchange2007_SP1')
        self.assertEqual(Build(8, 3).api_version(), 'Exchange2007_SP1')
        self.assertEqual(Build(15, 0, 1, 1).api_version(), 'Exchange2013')
        self.assertEqual(Build(15, 0, 1, 1).api_version(), 'Exchange2013')
        self.assertEqual(Build(15, 0, 847, 0).api_version(), 'Exchange2013_SP1')
        with self.assertRaises(KeyError):
            Build(16, 0).api_version()
        with self.assertRaises(KeyError):
            Build(15, 4).api_version()


class VersionTest(unittest.TestCase):
    def test_default_api_version(self):
        # Test that a version gets a reasonable api_version value if we don't set one explicitly
        version = Version(build=Build(15, 1, 2, 3))
        self.assertEqual(version.api_version, 'Exchange2016')


class ConfigurationTest(unittest.TestCase):
    def test_hardcode_all(self):
        # Test that we can hardcode everything without having a working server. This is useful if neither tasting or
        # guessing missing values works.
        Configuration(
            server='example.com',
            has_ssl=True,
            credentials=Credentials('foo', 'bar'),
            auth_type=NTLM,
            verify_ssl=True,
            version=Version(build=Build(15, 1, 2, 3), api_version='foo'),
        )


class CredentialsTest(unittest.TestCase):
    def test_hash(self):
        # Test that we can use credentials as a dict key
        self.assertEqual(hash(Credentials('a', 'b')), hash(Credentials('a', 'b')))
        self.assertNotEqual(hash(Credentials('a', 'b')), hash(Credentials('a', 'a')))
        self.assertNotEqual(hash(Credentials('a', 'b')), hash(Credentials('b', 'b')))

    def test_equality(self):
        self.assertEqual(Credentials('a', 'b'), Credentials('a', 'b'))
        self.assertNotEqual(Credentials('a', 'b'), Credentials('a', 'a'))
        self.assertNotEqual(Credentials('a', 'b'), Credentials('b', 'b'))

    def test_type(self):
        self.assertEqual(Credentials('a', 'b').type, Credentials.UPN)
        self.assertEqual(Credentials('a@example.com', 'b').type, Credentials.EMAIL)
        self.assertEqual(Credentials('a\\n', 'b').type, Credentials.DOMAIN)


class EWSDateTimeTest(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None

    def test_ewstimezone(self):
        # Test autogenerated translations
        tz = EWSTimeZone.timezone('Europe/Copenhagen')
        self.assertIsInstance(tz, EWSTimeZone)
        self.assertEqual(tz.zone, 'Europe/Copenhagen')
        self.assertEqual(tz.ms_id, 'Romance Standard Time')
        # self.assertEqual(EWSTimeZone.timezone('Europe/Copenhagen').ms_name, '')  # EWS works fine without the ms_name

        # Test common helpers
        tz = EWSTimeZone.timezone('UTC')
        self.assertIsInstance(tz, EWSTimeZone)
        self.assertEqual(tz.zone, 'UTC')
        self.assertEqual(tz.ms_id, 'UTC')
        tz = EWSTimeZone.timezone('GMT')
        self.assertIsInstance(tz, EWSTimeZone)
        self.assertEqual(tz.zone, 'GMT')
        self.assertEqual(tz.ms_id, 'GMT Standard Time')

        # Test mapper contents. Latest map from unicode.org has 394 entries
        self.assertGreater(len(EWSTimeZone.PYTZ_TO_MS_MAP), 300)
        for k, v in EWSTimeZone.PYTZ_TO_MS_MAP.items():
            self.assertIsInstance(k, str)
            self.assertIsInstance(v, str)

        # Test timezone unknown by pytz
        with self.assertRaises(UnknownTimeZone):
            EWSTimeZone.timezone('UNKNOWN')

        # Test timezone known by pytz but with no Winzone mapping
        import pytz
        tz = pytz.timezone('Africa/Tripoli')
        # This hack smashes the pytz timezone cache. Don't reuse the original timezone name for other tests
        tz.zone = 'UNKNOWN'
        with self.assertRaises(ValueError):
            EWSTimeZone.from_pytz(tz)

    def test_ewsdatetime(self):
        tz = EWSTimeZone.timezone('Europe/Copenhagen')
        dt = tz.localize(EWSDateTime(2000, 1, 2, 3, 4, 5))
        self.assertIsInstance(dt, EWSDateTime)
        self.assertIsInstance(dt.tzinfo, EWSTimeZone)
        self.assertEqual(dt.tzinfo.ms_id, tz.ms_id)
        self.assertEqual(dt.tzinfo.ms_name, tz.ms_name)
        self.assertEqual(str(dt), '2000-01-02 03:04:05+01:00')
        self.assertEqual(
            repr(dt),
            "EWSDateTime(2000, 1, 2, 3, 4, 5, tzinfo=<DstTzInfo 'Europe/Copenhagen' CET+1:00:00 STD>)"
        )
        self.assertIsInstance(dt + datetime.timedelta(days=1), EWSDateTime)
        self.assertIsInstance(dt - datetime.timedelta(days=1), EWSDateTime)
        self.assertIsInstance(dt - EWSDateTime.now(tz=tz), datetime.timedelta)
        self.assertIsInstance(EWSDateTime.now(tz=tz), EWSDateTime)
        self.assertEqual(dt, EWSDateTime.from_datetime(tz.localize(datetime.datetime(2000, 1, 2, 3, 4, 5))))
        self.assertEqual(dt.ewsformat(), '2000-01-02T03:04:05')
        utc_tz = EWSTimeZone.timezone('UTC')
        self.assertEqual(dt.astimezone(utc_tz).ewsformat(), '2000-01-02T02:04:05Z')
        # Test summertime
        dt = tz.localize(EWSDateTime(2000, 8, 2, 3, 4, 5))
        self.assertEqual(dt.astimezone(utc_tz).ewsformat(), '2000-08-02T01:04:05Z')
        # Test error when tzinfo is set directly
        with self.assertRaises(ValueError):
            EWSDateTime(2000, 1, 1, tzinfo=tz)
        # Test normalize, for completeness
        self.assertEqual(tz.normalize(tz.localize(EWSDateTime(2000, 1, 1))).ewsformat(), '2000-01-01T00:00:00')

    def test_generate(self):
        try:
            self.assertDictEqual(generate_map(), PYTZ_TO_MS_TIMEZONE_MAP)
        except CONNECTION_ERRORS:
            # generate_map() requires access to unicode.org, which may be unavailable. Don't fail test, since this is
            # out of our control.
            pass

    def test_ewsdate(self):
        self.assertEqual(EWSDate(2000, 1, 1).ewsformat(), '2000-01-01')


class RestrictionTest(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None

    def test_q(self):
        tz = EWSTimeZone.timezone('Europe/Copenhagen')
        start = tz.localize(EWSDateTime(1900, 9, 26, 8, 0, 0))
        end = tz.localize(EWSDateTime(2200, 9, 26, 11, 0, 0))
        result = '''\
<m:Restriction>
    <t:And>
        <t:Or>
            <t:Contains ContainmentComparison="Exact" ContainmentMode="Substring">
                <t:FieldURI FieldURI="item:Categories" />
                <t:Constant Value="FOO" />
            </t:Contains>
            <t:Contains ContainmentComparison="Exact" ContainmentMode="Substring">
                <t:FieldURI FieldURI="item:Categories" />
                <t:Constant Value="BAR" />
            </t:Contains>
        </t:Or>
        <t:IsGreaterThan>
            <t:FieldURI FieldURI="calendar:End" />
            <t:FieldURIOrConstant>
                <t:Constant Value="1900-09-26T07:10:00Z" />
            </t:FieldURIOrConstant>
        </t:IsGreaterThan>
        <t:IsLessThan>
            <t:FieldURI FieldURI="calendar:Start" />
            <t:FieldURIOrConstant>
                <t:Constant Value="2200-09-26T10:00:00Z" />
            </t:FieldURIOrConstant>
        </t:IsLessThan>
    </t:And>
</m:Restriction>'''
        q = Q(Q(categories__contains='FOO') | Q(categories__contains='BAR'), start__lt=end, end__gt=start)
        r = Restriction(q, folder_class=Calendar)
        self.assertEqual(str(r), ''.join(l.lstrip() for l in result.split('\n')))
        # Test empty Q
        q = Q()
        self.assertEqual(q.to_xml(folder_class=Calendar), None)
        with self.assertRaises(ValueError):
            Restriction(q, folder_class=Calendar)

    def test_q_expr(self):
        self.assertEqual(Q().expr(), None)
        self.assertEqual((~Q()).expr(), None)
        self.assertEqual(Q(x=5).expr(), 'x == 5')
        self.assertEqual((~Q(x=5)).expr(), 'x != 5')
        q = (Q(b__contains='a', x__contains=5) | Q(~Q(a__contains='c'), f__gt=3, c=6)) & ~Q(y=9, z__contains='b')
        self.assertEqual(
            q.expr(),
            "((b contains 'a' AND x contains 5) OR (NOT a contains 'c' AND c == 6 AND f > 3)) "
            "AND NOT (y == 9 AND z contains 'b')"
        )
        # Test simulated IN expression
        in_q = Q(foo__in=[1, 2, 3])
        self.assertEqual(in_q.conn_type, Q.OR)
        self.assertEqual(len(in_q.children), 3)

    def test_q_inversion(self):
        self.assertEqual((~Q(foo=5)).op, Q.NE)
        self.assertEqual((~Q(foo__not=5)).op, Q.EQ)
        self.assertEqual((~Q(foo__lt=5)).op, Q.GTE)
        self.assertEqual((~Q(foo__lte=5)).op, Q.GT)
        self.assertEqual((~Q(foo__gt=5)).op, Q.LTE)
        self.assertEqual((~Q(foo__gte=5)).op, Q.LT)

    def test_q_boolean_ops(self):
        self.assertEqual((Q(foo=5) & Q(foo=6)).conn_type, Q.AND)
        self.assertEqual((Q(foo=5) | Q(foo=6)).conn_type, Q.OR)

    def test_q_failures(self):
        with self.assertRaises(ValueError):
            # Invalid lookup
            Q(foo__XXX=5)
        with self.assertRaises(ValueError):
            # Invalid value
            Q(foo=None)


class QuerySetTest(unittest.TestCase):
    @python_2_unicode_compatible
    class MockAccount(Account):
        def __init__(self):
            pass

        def __str__(self):
            return ''

    def test_from_folder(self):
        folder = Inbox(account=self.MockAccount())
        self.assertIsInstance(folder.all(), QuerySet)
        self.assertIsInstance(folder.none(), QuerySet)
        self.assertIsInstance(folder.filter(subject='foo'), QuerySet)
        self.assertIsInstance(folder.exclude(subject='foo'), QuerySet)

    def test_queryset_copy(self):
        qs = QuerySet(folder=Inbox(account=self.MockAccount()))
        qs.q = Q()
        qs.only_fields = ('a', 'b')
        qs.order_fields = ('c', 'd')
        qs.return_format = QuerySet.NONE

        # Initially, immutable items have the same id()
        new_qs = qs.copy()
        self.assertNotEqual(id(qs), id(new_qs))
        self.assertEqual(id(qs.folder), id(new_qs.folder))
        self.assertEqual(id(qs._cache), id(new_qs._cache))
        self.assertEqual(qs._cache, new_qs._cache)
        self.assertNotEqual(id(qs.q), id(new_qs.q))
        self.assertEqual(qs.q, new_qs.q)
        self.assertEqual(id(qs.only_fields), id(new_qs.only_fields))
        self.assertEqual(qs.only_fields, new_qs.only_fields)
        self.assertEqual(id(qs.order_fields), id(new_qs.order_fields))
        self.assertEqual(qs.order_fields, new_qs.order_fields)
        self.assertEqual(id(qs.return_format), id(new_qs.return_format))
        self.assertEqual(qs.return_format, new_qs.return_format)

        # Set the same values, forcing a new id()
        new_qs.q = Q()
        new_qs.only_fields = ('a', 'b')
        new_qs.order_fields = ('c', 'd')
        new_qs.return_format = QuerySet.NONE

        self.assertNotEqual(id(qs), id(new_qs))
        self.assertEqual(id(qs.folder), id(new_qs.folder))
        self.assertEqual(id(qs._cache), id(new_qs._cache))
        self.assertEqual(qs._cache, new_qs._cache)
        self.assertNotEqual(id(qs.q), id(new_qs.q))
        self.assertEqual(qs.q, new_qs.q)
        self.assertNotEqual(id(qs.only_fields), id(new_qs.only_fields))
        self.assertEqual(qs.only_fields, new_qs.only_fields)
        self.assertNotEqual(id(qs.order_fields), id(new_qs.order_fields))
        self.assertEqual(qs.order_fields, new_qs.order_fields)
        self.assertEqual(id(qs.return_format), id(new_qs.return_format))  # String literals are also singletons
        self.assertEqual(qs.return_format, new_qs.return_format)

        # Set the new values, forcing a new id()
        new_qs.q = Q(foo=5)
        new_qs.only_fields = ('c', 'd')
        new_qs.order_fields = ('e', 'f')
        new_qs.return_format = QuerySet.VALUES

        self.assertNotEqual(id(qs), id(new_qs))
        self.assertEqual(id(qs.folder), id(new_qs.folder))
        self.assertEqual(id(qs._cache), id(new_qs._cache))
        self.assertEqual(qs._cache, new_qs._cache)
        self.assertNotEqual(id(qs.q), id(new_qs.q))
        self.assertNotEqual(qs.q, new_qs.q)
        self.assertNotEqual(id(qs.only_fields), id(new_qs.only_fields))
        self.assertNotEqual(qs.only_fields, new_qs.only_fields)
        self.assertNotEqual(id(qs.order_fields), id(new_qs.order_fields))
        self.assertNotEqual(qs.order_fields, new_qs.order_fields)
        self.assertNotEqual(id(qs.return_format), id(new_qs.return_format))
        self.assertNotEqual(qs.return_format, new_qs.return_format)


class UtilTest(unittest.TestCase):
    def test_chunkify(self):
        # Test tuple, list, set, range, map and generator
        seq = [1, 2, 3, 4, 5]
        self.assertEqual(list(chunkify(seq, chunksize=2)), [[1, 2], [3, 4], [5]])

        seq = (1, 2, 3, 4, 6, 7, 9)
        self.assertEqual(list(chunkify(seq, chunksize=3)), [(1, 2, 3), (4, 6, 7), (9,)])

        seq = {1, 2, 3, 4, 5}
        self.assertEqual(list(chunkify(seq, chunksize=2)), [[1, 2], [3, 4], [5, ]])

        seq = range(5)
        self.assertEqual(list(chunkify(seq, chunksize=2)), [range(0, 2), range(2, 4), range(4, 5)])

        seq = map(int, range(5))
        self.assertEqual(list(chunkify(seq, chunksize=2)), [[0, 1], [2, 3], [4]])

        seq = (i for i in range(5))
        self.assertEqual(list(chunkify(seq, chunksize=2)), [[0, 1], [2, 3], [4]])

    def test_peek(self):
        # Test peeking into various sequence types

        # tuple
        is_empty, seq = peek(tuple())
        self.assertEqual((is_empty, list(seq)), (True, []))
        is_empty, seq = peek((1, 2, 3))
        self.assertEqual((is_empty, list(seq)), (False, [1, 2, 3]))

        # list
        is_empty, seq = peek([])
        self.assertEqual((is_empty, list(seq)), (True, []))
        is_empty, seq = peek([1, 2, 3])
        self.assertEqual((is_empty, list(seq)), (False, [1, 2, 3]))

        # set
        is_empty, seq = peek(set())
        self.assertEqual((is_empty, list(seq)), (True, []))
        is_empty, seq = peek({1, 2, 3})
        self.assertEqual((is_empty, list(seq)), (False, [1, 2, 3]))

        # range
        is_empty, seq = peek(range(0))
        self.assertEqual((is_empty, list(seq)), (True, []))
        is_empty, seq = peek(range(1, 4))
        self.assertEqual((is_empty, list(seq)), (False, [1, 2, 3]))

        # map
        is_empty, seq = peek(map(int, []))
        self.assertEqual((is_empty, list(seq)), (True, []))
        is_empty, seq = peek(map(int, [1, 2, 3]))
        self.assertEqual((is_empty, list(seq)), (False, [1, 2, 3]))

        # generator
        is_empty, seq = peek((i for i in []))
        self.assertEqual((is_empty, list(seq)), (True, []))
        is_empty, seq = peek((i for i in [1, 2, 3]))
        self.assertEqual((is_empty, list(seq)), (False, [1, 2, 3]))

    def test_get_redirect_url(self):
        r = requests.get('https://httpbin.org/redirect-to?url=https://example.com/', allow_redirects=False)
        self.assertEqual(get_redirect_url(r), 'https://example.com/')
        r = requests.get('https://httpbin.org/redirect-to?url=http://example.com/', allow_redirects=False)
        self.assertEqual(get_redirect_url(r), 'http://example.com/')
        r = requests.get('https://httpbin.org/redirect-to?url=/example', allow_redirects=False)
        self.assertEqual(get_redirect_url(r), 'https://httpbin.org/example')
        with self.assertRaises(RelativeRedirect):
            r = requests.get('https://httpbin.org/redirect-to?url=https://example.com', allow_redirects=False)
            get_redirect_url(r, require_relative=True)
        with self.assertRaises(RelativeRedirect):
            r = requests.get('https://httpbin.org/redirect-to?url=/example', allow_redirects=False)
            get_redirect_url(r, allow_relative=False)

    def test_to_xml(self):
        to_xml('<?xml version="1.0" encoding="UTF-8"?><foo></foo>', encoding='ascii')
        to_xml(BOM+'<?xml version="1.0" encoding="UTF-8"?><foo></foo>', encoding='ascii')
        to_xml(BOM+'<?xml version="1.0" encoding="UTF-8"?><foo>&broken</foo>', encoding='ascii')
        with self.assertRaises(ParseError):
            to_xml('foo', encoding='ascii')

    def test_get_domain(self):
        self.assertEqual(get_domain('foo@example.com'), 'example.com')
        with self.assertRaises(ValueError):
            get_domain('blah')


class EWSTest(unittest.TestCase):
    def setUp(self):
        # There's no official Exchange server we can test against, and we can't really provide credentials for our
        # own test server to everyone on the Internet. Travis-CI uses the encrypted settings.yml.enc for testing.
        #
        # If you want to test against your own server and account, create your own settings.yml with credentials for
        # that server. 'settings.yml.sample' is provided as a template.
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'settings.yml')) as f:
                settings = load(f)
        except FileNotFoundError:
            print('Skipping %s - no settings.yml file found' % self.__class__.__name__)
            print('Copy settings.yml.sample to settings.yml and enter values for your test server')
            raise unittest.SkipTest('Skipping %s - no settings.yml file found' % self.__class__.__name__)
        self.tz = EWSTimeZone.timezone('Europe/Copenhagen')
        self.categories = [get_random_string(length=10, spaces=False, special=False)]
        self.config = Configuration(server=settings['server'],
                                    credentials=Credentials(settings['username'], settings['password']),
                                    verify_ssl=settings['verify_ssl'])
        self.account = Account(primary_smtp_address=settings['account'], access_type=DELEGATE, config=self.config, locale='da_DK')
        self.maxDiff = None

    def bulk_delete(self, ids):
        # Clean up items and check return values
        for res in self.account.bulk_delete(ids, affected_task_occurrences=ALL_OCCURRENCIES):
            self.assertEqual(res, True)

    def random_val(self, field):
        if isinstance(field, ExtendedPropertyField):
            if field.value_cls.is_array_type():
                if field.value_cls.python_type() == string_type:
                    return [get_random_string(255) for _ in range(random.randint(1, 4))]
                if field.value_cls.python_type() == int:
                    return [get_random_int(0, 256) for _ in range(random.randint(1, 4))]
                if field.value_cls.python_type() == bytes:
                    return [get_random_string(255).encode() for _ in range(random.randint(1, 4))]
            if field.value_cls.python_type() == string_type:
                return get_random_string(255)
            if field.value_cls.python_type() == int:
                return get_random_int(0, 256)
            if field.value_cls.python_type() == bytes:
                return get_random_string(255).encode()
            assert False, (field.name, field, field.value_cls.python_type())
        if isinstance(field, URIField):
            return get_random_url()
        if isinstance(field, EmailField):
            return get_random_email()
        if isinstance(field, ChoiceField):
            return get_random_choice(field.choices)
        if isinstance(field, BodyField):
            return get_random_string(255)
        if isinstance(field, TextField):
            if field.is_list:
                return [get_random_string(16) for _ in range(random.randint(1, 4))]
            return get_random_string(field.max_length or 255)
        if isinstance(field, Base64Field):
            return get_random_string(255)
        if isinstance(field, BooleanField):
            return get_random_bool()
        if isinstance(field, IntegerField):
            return get_random_int(0, 256)
        if isinstance(field, DecimalField):
            return get_random_decimal(1, 99)
        if isinstance(field, DateTimeField):
            return get_random_datetime()
        if isinstance(field, EWSElementField):
            if field.value_cls == MessageHeader:
                return [MessageHeader(name=get_random_string(10), value=get_random_string(255))
                        for _ in range(random.randint(1, 4))]
            if isinstance(field, AttachmentField):
                return [FileAttachment(name='my_file.txt', content=b'test_content')]
            if isinstance(field, MailboxField):
                # email_address must be a real account on the server(?)
                # TODO: Mailbox has multiple optional args, but they must match the server account, so we can't easily test.
                if get_random_bool():
                    val = Mailbox(email_address=self.account.primary_smtp_address)
                else:
                    val = self.account.primary_smtp_address
                return [val] if field.is_list else val
            if isinstance(field, AttendeesField):
                # Attendee must refer to a real mailbox on the server(?). We're only sure to have one
                if get_random_bool():
                    mbx = Mailbox(email_address=self.account.primary_smtp_address)
                else:
                    mbx = self.account.primary_smtp_address
                with_last_response_time = get_random_bool()
                if with_last_response_time:
                    val = Attendee(mailbox=mbx, response_type='Accept', last_response_time=get_random_datetime())
                else:
                    if get_random_bool():
                        val = Attendee(mailbox=mbx, response_type='Accept')
                    else:
                        val = self.account.primary_smtp_address
                return [val] if field.is_list else val
        if isinstance(field, IndexedField):
            if isinstance(field, EmailAddressField):
                if field.is_list:
                    addrs = []
                    for label in EmailAddress.LABELS:
                        addr = EmailAddress(email=get_random_email())
                        addr.label = label
                        addrs.append(addr)
                    return addrs
                return EmailAddress(email=get_random_email())
            if isinstance(field, PhysicalAddressField):
                addrs = []
                for label in PhysicalAddress.LABELS:
                    addr = PhysicalAddress(street=get_random_string(32), city=get_random_string(32),
                                           state=get_random_string(32), country=get_random_string(32),
                                           zipcode=get_random_string(8))
                    addr.label = label
                    addrs.append(addr)
                return addrs
            if isinstance(field, PhoneNumberField):
                pns = []
                for label in PhoneNumber.LABELS:
                    pn = PhoneNumber(phone_number=get_random_string(16))
                    pn.label = label
                    pns.append(pn)
                return pns
        assert False, 'Unknown field %s' % field


class CommonTest(EWSTest):
    def test_wrap(self):
        # Test payload wrapper with both delegation, impersonation and timezones
        MockAccount = namedtuple('Account', ['access_type', 'primary_smtp_address'])
        MockTZ = namedtuple('EWSTimeZone', ['ms_id'])
        content = create_element('AAA')
        version = 'BBB'
        account = MockAccount(DELEGATE, 'foo@example.com')
        tz = MockTZ('XXX')
        wrapped = wrap(content=content, version=version, account=account)
        self.assertEqual(
            wrapped,
            b''.join(l.strip() for l in b'''\
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
    <s:Header>
        <t:RequestServerVersion Version="BBB" />
    </s:Header>
    <s:Body>
        <AAA />
    </s:Body>
</s:Envelope>'''.split(b'\n')))

        account = MockAccount(IMPERSONATION, 'foo@example.com')
        wrapped = wrap(content=content, version=version, account=account, encoding='utf-8')
        self.assertEqual(
            wrapped,
            b''.join(l.strip() for l in b'''\
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
    <s:Header>
        <t:RequestServerVersion Version="BBB" />
        <t:ExchangeImpersonation>
            <t:ConnectingSID>
                <t:PrimarySmtpAddress>foo@example.com</t:PrimarySmtpAddress>
            </t:ConnectingSID>
        </t:ExchangeImpersonation>
    </s:Header>
    <s:Body>
        <AAA />
    </s:Body>
</s:Envelope>'''.split(b'\n')))

        wrapped = wrap(content=content, version=version, account=account, ewstimezone=tz, encoding='latin1')
        self.assertEqual(
            wrapped,
            b''.join(l.strip() for l in b'''\
<?xml version="1.0" encoding="latin1"?>
<s:Envelope xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
    <s:Header>
        <t:RequestServerVersion Version="BBB" />
        <t:ExchangeImpersonation>
            <t:ConnectingSID>
                <t:PrimarySmtpAddress>foo@example.com</t:PrimarySmtpAddress>
            </t:ConnectingSID>
        </t:ExchangeImpersonation>
        <t:TimeZoneContext>
            <t:TimeZoneDefinition Id="XXX" />
        </t:TimeZoneContext>
    </s:Header>
    <s:Body>
        <AAA />
    </s:Body>
</s:Envelope>'''.split(b'\n')))

    def test_poolsize(self):
        self.assertEqual(self.config.protocol.SESSION_POOLSIZE, 4)

    def test_get_timezones(self):
        ws = GetServerTimeZones(self.config.protocol)
        data = ws.call()
        self.assertAlmostEqual(len(data), 130, delta=30, msg=data)
        # Test shortcut
        self.assertAlmostEqual(len(self.config.protocol.get_timezones()), 130, delta=30, msg=data)

    def test_get_roomlists(self):
        # The test server is not guaranteed to have any room lists which makes this test less useful
        ws = GetRoomLists(self.config.protocol)
        roomlists = ws.call()
        self.assertEqual(roomlists, [])
        # Test shortcut
        self.assertEqual(self.config.protocol.get_roomlists(), [])

    def test_get_roomlists_parsing(self):
        # Test static XML since server has no roomlists
        ws = GetRoomLists(self.config.protocol)
        xml = '''\
<?xml version="1.0" ?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
    <s:Header>
        <h:ServerVersionInfo
            MajorBuildNumber="845" MajorVersion="15" MinorBuildNumber="22" MinorVersion="1" Version="V2016_10_10"
            xmlns:h="http://schemas.microsoft.com/exchange/services/2006/types"
            xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>
    </s:Header>
    <s:Body>
        <m:GetRoomListsResponse ResponseClass="Success"
                xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
                xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
                xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <m:ResponseCode>NoError</m:ResponseCode>
            <m:RoomLists>
                <t:Address>
                    <t:Name>Roomlist</t:Name>
                    <t:EmailAddress>roomlist1@example.com</t:EmailAddress>
                    <t:RoutingType>SMTP</t:RoutingType>
                    <t:MailboxType>PublicDL</t:MailboxType>
                </t:Address>
                <t:Address>
                    <t:Name>Roomlist</t:Name>
                    <t:EmailAddress>roomlist2@example.com</t:EmailAddress>
                    <t:RoutingType>SMTP</t:RoutingType>
                    <t:MailboxType>PublicDL</t:MailboxType>
                </t:Address>
            </m:RoomLists>
        </m:GetRoomListsResponse>
    </s:Body>
</s:Envelope>'''
        res = ws._get_elements_in_response(response=ws._get_soap_payload(soap_response=to_xml(xml, 'utf-8')))
        self.assertSetEqual(
            {RoomList.from_xml(elem).email_address for elem in res},
            {'roomlist1@example.com', 'roomlist2@example.com'}
        )

    def test_get_rooms(self):
        # The test server is not guaranteed to have any rooms or room lists which makes this test less useful
        roomlist = RoomList(email_address='my.roomlist@example.com')
        ws = GetRooms(self.config.protocol)
        with self.assertRaises(ErrorNameResolutionNoResults):
            ws.call(roomlist=roomlist)
        # Test shortcut
        with self.assertRaises(ErrorNameResolutionNoResults):
            self.config.protocol.get_rooms('my.roomlist@example.com')

    def test_get_rooms_parsing(self):
        # Test static XML since server has no rooms
        ws = GetRooms(self.config.protocol)
        xml = '''\
<?xml version="1.0" ?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
    <s:Header>
        <h:ServerVersionInfo
            MajorBuildNumber="845" MajorVersion="15" MinorBuildNumber="22" MinorVersion="1" Version="V2016_10_10"
            xmlns:h="http://schemas.microsoft.com/exchange/services/2006/types"
            xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>
    </s:Header>
    <s:Body>
        <m:GetRoomsResponse ResponseClass="Success"
                xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
                xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
                xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <m:ResponseCode>NoError</m:ResponseCode>
            <m:Rooms>
                <t:Room>
                    <t:Id>
                        <t:Name>room1</t:Name>
                        <t:EmailAddress>room1@example.com</t:EmailAddress>
                        <t:RoutingType>SMTP</t:RoutingType>
                        <t:MailboxType>Mailbox</t:MailboxType>
                    </t:Id>
                </t:Room>
                <t:Room>
                    <t:Id>
                        <t:Name>room2</t:Name>
                        <t:EmailAddress>room2@example.com</t:EmailAddress>
                        <t:RoutingType>SMTP</t:RoutingType>
                        <t:MailboxType>Mailbox</t:MailboxType>
                    </t:Id>
                </t:Room>
            </m:Rooms>
        </m:GetRoomsResponse>
    </s:Body>
</s:Envelope>'''
        res = ws._get_elements_in_response(response=ws._get_soap_payload(soap_response=to_xml(xml, 'utf-8')))
        self.assertSetEqual(
            {Room.from_xml(elem).email_address for elem in res},
            {'room1@example.com', 'room2@example.com'}
        )

    def test_sessionpool(self):
        # First, empty the calendar
        start = self.tz.localize(EWSDateTime(2011, 10, 12, 8))
        end = self.tz.localize(EWSDateTime(2011, 10, 12, 10))
        self.account.calendar.filter(start__lt=end, end__gt=start, categories__contains=self.categories).delete()
        items = []
        for i in range(75):
            subject = 'Test Subject %s' % i
            item = CalendarItem(
                start=start,
                end=end,
                subject=subject,
                categories=self.categories,
            )
            items.append(item)
        return_ids = self.account.calendar.bulk_create(items=items)
        self.assertEqual(len(return_ids), len(items))
        ids = self.account.calendar.filter(start__lt=end, end__gt=start, categories__contains=self.categories) \
            .values_list('item_id', 'changekey')
        self.assertEqual(len(ids), len(items))
        return_items = list(self.account.fetch(return_ids))
        self.bulk_delete(return_items)

    def test_magic(self):
        self.assertIn(self.config.protocol.version.api_version, str(self.config.protocol))
        self.assertIn(self.config.credentials.username, str(self.config.credentials))
        self.assertIn(self.account.primary_smtp_address, str(self.account))
        self.assertIn(str(self.account.version.build.major_version), repr(self.account.version))
        for item in (
                self.config,
                self.config.protocol,
                self.account.version,
                self.account.trash,
                self.account.drafts,
                self.account.inbox,
                self.account.outbox,
                self.account.sent,
                self.account.junk,
                self.account.contacts,
                self.account.tasks,
                self.account.calendar,
                self.account.recoverable_items_root,
                self.account.recoverable_deleted_items,
        ):
            # Just test that these at least don't throw errors
            repr(item)
            str(item)

    def test_configuration(self):
        with self.assertRaises(AttributeError):
            Configuration(credentials=Credentials(username='foo', password='bar'))
        with self.assertRaises(ValueError):
            Configuration(credentials=Credentials(username='foo', password='bar'),
                          service_endpoint='http://example.com/svc',
                          auth_type='XXX')

    def test_failed_login(self):
        with self.assertRaises(UnauthorizedError):
            Configuration(
                service_endpoint=self.config.protocol.service_endpoint,
                credentials=Credentials(self.config.protocol.credentials.username, 'WRONG_PASSWORD'),
                verify_ssl=self.config.protocol.verify_ssl)
        with self.assertRaises(AutoDiscoverFailed):
            Account(
                primary_smtp_address=self.account.primary_smtp_address,
                access_type=DELEGATE,
                credentials=Credentials(self.config.protocol.credentials.username, 'WRONG_PASSWORD'),
                autodiscover=True,
                locale='da_DK')

    def test_post_ratelimited(self):
        url = 'https://example.com'

        def mock_session_post(status_code, headers, text):
            req = namedtuple('request', ['headers'])(headers={})
            return lambda **kwargs: namedtuple(
                'response', ['status_code', 'headers', 'text', 'request', 'history', 'url']
            )(status_code=status_code, headers=headers, text=text, request=req, history=None, url=url)

        def mock_session_exception(exc_cls):
            def raise_exc(**kwargs):
                raise exc_cls()
            return raise_exc

        protocol = self.config.protocol
        credentials = protocol.credentials
        # Make sure we fail fast in error cases
        protocol.credentials = Credentials(username=credentials.username, password=credentials.password)

        session = protocol.get_session()

        # Test the straight, HTTP 200 path
        session.post = mock_session_post(200, {}, 'foo')
        r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')
        self.assertEqual(r.text, 'foo')

        # Test exceptions raises by the POST request
        for exc_cls in CONNECTION_ERRORS:
            session.post = mock_session_exception(exc_cls)
            with self.assertRaises(exc_cls):
                r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')

        # Test bad exit codes and headers
        session.post = mock_session_post(401, {}, '')
        with self.assertRaises(UnauthorizedError):
            r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')
        session.post = mock_session_post(999, {'connection': 'close'}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')
        session.post = mock_session_post(302, {'location': '/ews/genericerrorpage.htm?aspxerrorpath=/ews/exchange.asmx'}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')
        session.post = mock_session_post(503, {}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')

        # No redirect header
        session.post = mock_session_post(302, {}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')
        # Redirect header to same location
        session.post = mock_session_post(302, {'location': url}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')
        # Redirect header to relative location
        session.post = mock_session_post(302, {'location': url + '/foo'}, '')
        with self.assertRaises(RedirectError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')
        # Redirect header to other location and allow_redirects=False
        session.post = mock_session_post(302, {'location': 'https://contoso.com'}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')
        # Redirect header to other location and allow_redirects=True
        import exchangelib.util
        exchangelib.util.MAX_REDIRECTS = 0
        session.post = mock_session_post(302, {'location': 'https://contoso.com'}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='',
                                          allow_redirects=True)

        # CAS error
        session.post = mock_session_post(999, {'X-CasErrorCode': 'AAARGH!'}, '')
        with self.assertRaises(CASError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')

        # Allow XML data in a non-HTTP 200 response
        session.post = mock_session_post(500, {}, '<?xml version="1.0" ?><foo></foo>')
        r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')
        self.assertEqual(r.text, '<?xml version="1.0" ?><foo></foo>')

        # Bad status_code and bad text
        session.post = mock_session_post(999, {}, '')
        with self.assertRaises(TransportError):
            r, session = post_ratelimited(protocol=protocol, session=session, url=url, headers=None, data='')

        # Rate limit exceeded
        protocol.credentials = ServiceAccount(username=credentials.username, password=credentials.password, max_wait=1)
        session.post = mock_session_post(503, {'connection': 'close'}, '')
        protocol.renew_session = lambda s: s  # Return the same session so it's still mocked
        with self.assertRaises(RateLimitError):
            r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')
        # Test something larger than the default wait, so we retry at least once
        protocol.credentials.max_wait = 15
        session.post = mock_session_post(503, {'connection': 'close'}, '')
        with self.assertRaises(RateLimitError):
            r, session = post_ratelimited(protocol=protocol, session=session, url='', headers=None, data='')

        protocol.release_session(session)
        protocol.credentials = credentials

    def test_soap_error(self):
        soap_xml = """\
<?xml version="1.0" encoding="utf-8" ?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Header>
    <t:ServerVersionInfo MajorVersion="8" MinorVersion="0" MajorBuildNumber="685" MinorBuildNumber="8"
                         xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types" />
  </soap:Header>
  <soap:Body>
    <soap:Fault>
      <faultcode>{faultcode}</faultcode>
      <faultstring>{faultstring}</faultstring>
      <faultactor>https://CAS01.example.com/EWS/Exchange.asmx</faultactor>
      <detail>
        <ResponseCode xmlns="http://schemas.microsoft.com/exchange/services/2006/errors">{responsecode}</ResponseCode>
        <Message xmlns="http://schemas.microsoft.com/exchange/services/2006/errors">{message}</Message>
      </detail>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>"""
        with self.assertRaises(SOAPError) as e:
            ResolveNames._get_soap_payload(to_xml(soap_xml.format(
                faultcode='YYY', faultstring='AAA', responsecode='XXX', message='ZZZ'
            ), encoding='utf-8'))
        self.assertIn('AAA', e.exception.args[0])
        self.assertIn('YYY', e.exception.args[0])
        self.assertIn('ZZZ', e.exception.args[0])
        with self.assertRaises(ErrorNonExistentMailbox) as e:
            ResolveNames._get_soap_payload(to_xml(soap_xml.format(
                faultcode='ErrorNonExistentMailbox', faultstring='AAA', responsecode='XXX', message='ZZZ'
            ), encoding='utf-8'))
        self.assertIn('AAA', e.exception.args[0])
        with self.assertRaises(ErrorNonExistentMailbox) as e:
            ResolveNames._get_soap_payload(to_xml(soap_xml.format(
                faultcode='XXX', faultstring='AAA', responsecode='ErrorNonExistentMailbox', message='YYY'
            ), encoding='utf-8'))
        self.assertIn('YYY', e.exception.args[0])

    def test_from_xml(self):
        # Test for all EWSElement classes that they handle None as input to from_xml()
        import exchangelib
        for mod in (exchangelib.attachments, exchangelib.extended_properties, exchangelib.indexed_properties,
                       exchangelib.folders, exchangelib.items, exchangelib.properties):
            for k, v in vars(mod).items():
                if type(v) != type:
                    continue
                if not issubclass(v, EWSElement):
                    continue
                if issubclass(v, (Item, Folder)):
                    # These do not support None input
                    with self.assertRaises(Exception):
                        v.from_xml(None)
                    continue
                v.from_xml(None)  # This should work for all others


class AccountTest(EWSTest):
    def test_magic(self):
        self.account.fullname = 'John Doe'
        self.assertIn(self.account.primary_smtp_address, str(self.account))
        self.assertIn(self.account.fullname, str(self.account))

    def test_validation(self):
        with self.assertRaises(ValueError):
            # Must have valid email address
            Account(primary_smtp_address='blah')
        with self.assertRaises(AttributeError):
            # Autodiscover requires credentials
            Account(primary_smtp_address=self.account.primary_smtp_address, autodiscover=True)
        with self.assertRaises(AttributeError):
            # Autodiscover must not have config
            Account(primary_smtp_address=self.account.primary_smtp_address, config='FOO', autodiscover=True)
        with self.assertRaises(AttributeError):
            # Non-autodiscover requires a config
            Account(primary_smtp_address=self.account.primary_smtp_address, autodiscover=False)

    def test_get_default_folder(self):
        class MockCalendar(Calendar):
            pass
        # Test a normal folder lookup with GetFolder
        folder = self.account._get_default_folder(MockCalendar)
        self.assertIsInstance(folder, MockCalendar)
        self.assertNotEqual(folder.folder_id, None)
        self.assertEqual(folder.name, MockCalendar.LOCALIZED_NAMES[self.account.locale][0])

        class MockCalendar(Calendar):
            @classmethod
            def get_distinguished(cls, account, shape=None):
                raise ErrorAccessDenied('foo')

        # Test an indirect folder lookup with FindItems
        folder = self.account._get_default_folder(MockCalendar)
        self.assertIsInstance(folder, MockCalendar)
        self.assertEqual(folder.folder_id, None)
        self.assertEqual(folder.name, MockCalendar.DISTINGUISHED_FOLDER_ID)

        class MockCalendar(Calendar):
            @classmethod
            def get_distinguished(cls, account, shape=None):
                raise ErrorFolderNotFound('foo')

        # Test using the one folder of this folder type
        with self.assertRaises(ErrorFolderNotFound):
            # This fails because there are no folders of type MockCalendar
            self.account._get_default_folder(MockCalendar)

        _orig = Calendar.get_distinguished
        Calendar.get_distinguished = MockCalendar.get_distinguished
        folder = self.account._get_default_folder(Calendar)
        self.assertIsInstance(folder, Calendar)
        self.assertNotEqual(folder.folder_id, None)
        self.assertEqual(folder.name, MockCalendar.LOCALIZED_NAMES[self.account.locale][0])
        Calendar.get_distinguished = _orig


class AutodiscoverTest(EWSTest):
    def test_magic(self):
        from exchangelib.autodiscover import _autodiscover_cache
        # Just test we don't fail
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        str(_autodiscover_cache)
        repr(_autodiscover_cache)
        for protocol in _autodiscover_cache._protocols.values():
            str(protocol)
            repr(protocol)

    def test_autodiscover(self):
        primary_smtp_address, protocol = discover(email=self.account.primary_smtp_address,
                                                  credentials=self.config.credentials)
        self.assertEqual(primary_smtp_address, self.account.primary_smtp_address)
        self.assertEqual(protocol.service_endpoint.lower(), self.config.protocol.service_endpoint.lower())
        self.assertEqual(protocol.version.build, self.config.protocol.version.build)

    def test_autodiscover_failure(self):
        with self.assertRaises(ErrorNonExistentMailbox):
            discover(email='XXX.' + self.account.primary_smtp_address, credentials=self.config.credentials)

    def test_close_autodiscover_connections(self):
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        close_connections()

    def test_autodiscover_gc(self):
        from exchangelib.autodiscover import _autodiscover_cache
        # This is what Python garbage collection does
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        del _autodiscover_cache

    def test_autodiscover_direct_gc(self):
        from exchangelib.autodiscover import _autodiscover_cache
        # This is what Python garbage collection does
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        _autodiscover_cache.__del__()

    def test_autodiscover_cache(self):
        from exchangelib.autodiscover import _autodiscover_cache
        # Empty the cache
        _autodiscover_cache.clear()
        cache_key = (self.account.domain, self.config.credentials, self.config.protocol.verify_ssl)
        # Not cached
        self.assertNotIn(cache_key, _autodiscover_cache)
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        # Now it's cached
        self.assertIn(cache_key, _autodiscover_cache)
        # Make sure the cache can be looked by value, not by id(). This is important for multi-threading/processing
        self.assertIn((
            self.account.primary_smtp_address.split('@')[1],
            Credentials(self.config.credentials.username, self.config.credentials.password),
            True
        ), _autodiscover_cache)
        # Poison the cache. discover() must survive and rebuild the cache
        _autodiscover_cache[cache_key] = AutodiscoverProtocol(
            service_endpoint='https://example.com/blackhole.asmx',
            credentials=Credentials('leet_user', 'cannaguess'),
            auth_type=NTLM,
            verify_ssl=True
        )
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        self.assertIn(cache_key, _autodiscover_cache)
        # Make sure that the cache is actually used on the second call to discover()
        import exchangelib.autodiscover
        _orig = exchangelib.autodiscover._try_autodiscover
        def _mock(*args, **kwargs):
            raise NotImplementedError()
        exchangelib.autodiscover._try_autodiscover = _mock
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        # Fake that another thread added the cache entry into the persistent storage but we don't have it in our
        # in-memory cache. The cache should work anyway.
        _autodiscover_cache._protocols.clear()
        discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        exchangelib.autodiscover._try_autodiscover = _orig
        # Make sure we can delete cache entries even though we don't have it in our in-memory cache
        _autodiscover_cache._protocols.clear()
        del _autodiscover_cache[cache_key]
        # This should also work if the cache does not contain the entry anymore
        del _autodiscover_cache[cache_key]

    def test_autodiscover_from_account(self):
        from exchangelib.autodiscover import _autodiscover_cache
        _autodiscover_cache.clear()
        account = Account(primary_smtp_address=self.account.primary_smtp_address, credentials=self.config.credentials,
                          autodiscover=True, locale='da_DK')
        self.assertEqual(account.primary_smtp_address, self.account.primary_smtp_address)
        self.assertEqual(account.protocol.service_endpoint.lower(), self.config.protocol.service_endpoint.lower())
        self.assertEqual(account.protocol.version.build, self.config.protocol.version.build)
        # Make sure cache is full
        self.assertTrue((account.domain, self.config.credentials, True) in _autodiscover_cache)
        # Test that autodiscover works with a full cache
        account = Account(primary_smtp_address=self.account.primary_smtp_address, credentials=self.config.credentials,
                          autodiscover=True, locale='da_DK')
        self.assertEqual(account.primary_smtp_address, self.account.primary_smtp_address)
        # Test cache manipulation
        key = (account.domain, self.config.credentials, True)
        self.assertTrue(key in _autodiscover_cache)
        del _autodiscover_cache[key]
        self.assertFalse(key in _autodiscover_cache)
        del _autodiscover_cache

    def test_autodiscover_redirect(self):
        # Prime the cache
        email, p = discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        # Test that we can get another address back than the address we're looking up
        import exchangelib.autodiscover
        _orig = exchangelib.autodiscover._autodiscover_quick
        def _mock1(credentials, email, protocol):
            return 'john@example.com', p
        exchangelib.autodiscover._autodiscover_quick = _mock1
        test_email, p = discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        self.assertEqual(test_email, 'john@example.com')
        # Test that we can survive being asked to lookup with another address
        def _mock2(credentials, email, protocol):
            if email == 'xxxxxx@'+self.account.domain:
                raise ErrorNonExistentMailbox(email)
            raise AutoDiscoverRedirect(redirect_email='xxxxxx@'+self.account.domain)
        exchangelib.autodiscover._autodiscover_quick = _mock2
        with self.assertRaises(ErrorNonExistentMailbox):
            discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        # Test that we catch circular redirects
        def _mock3(credentials, email, protocol):
            raise AutoDiscoverRedirect(redirect_email=self.account.primary_smtp_address)
        exchangelib.autodiscover._autodiscover_quick = _mock3
        with self.assertRaises(AutoDiscoverCircularRedirect):
            discover(email=self.account.primary_smtp_address, credentials=self.config.credentials)
        exchangelib.autodiscover._autodiscover_quick = _orig

    def test_canonical_lookup(self):
        from exchangelib.autodiscover import _get_canonical_name
        self.assertEqual(_get_canonical_name('example.com'), None)
        self.assertEqual(_get_canonical_name('example.com.'), 'example.com')
        self.assertEqual(_get_canonical_name('example.XXXXX.'), None)

    def test_srv(self):
        from exchangelib.autodiscover import _get_hostname_from_srv
        with self.assertRaises(AutoDiscoverFailed):
            # Unknown doomain
            _get_hostname_from_srv('example.XXXXX.')
        with self.assertRaises(AutoDiscoverFailed):
            # No SRV record
            _get_hostname_from_srv('example.com.')
        # Finding a real server that has a correct SRV record is not easy. Mock it
        import dns.resolver
        _orig = dns.resolver.Resolver

        class _Mock1:
            def query(self, hostname, cat):
                class A:
                    def to_text(self):
                        # Return a valid record
                        return '1 2 3 example.com.'
                return [A()]
        dns.resolver.Resolver = _Mock1
        # Test a valid record
        self.assertEqual(_get_hostname_from_srv('example.com.'), 'example.com')

        class _Mock2:
            def query(self, hostname, cat):
                class A:
                    def to_text(self):
                        # Return malformed data
                        return 'XXXXXXX'
                return [A()]
        dns.resolver.Resolver = _Mock2
        # Test an invalid record
        with self.assertRaises(AutoDiscoverFailed):
            _get_hostname_from_srv('example.com.')
        dns.resolver.Resolver = _orig


class FolderTest(EWSTest):
    def test_folders(self):
        folders = self.account.folders
        for folder_cls, cls_folders in folders.items():
            for f in cls_folders:
                f.test_access()
        # Test shortcuts
        for f, cls in (
                (self.account.trash, DeletedItems),
                (self.account.drafts, Drafts),
                (self.account.inbox, Inbox),
                (self.account.outbox, Outbox),
                (self.account.sent, SentItems),
                (self.account.junk, JunkEmail),
                (self.account.contacts, Contacts),
                (self.account.tasks, Tasks),
                (self.account.calendar, Calendar),
        ):
            self.assertIsInstance(f, cls)
            f.test_access()
            # Test item field lookup
            self.assertEqual(f.get_item_field_by_fieldname('subject').name, 'subject')
            with self.assertRaises(ValueError):
                f.get_item_field_by_fieldname('XXX')

    def test_getfolders(self):
        folders = self.account.root.get_folders()
        self.assertGreater(len(folders), 60, sorted(f.name for f in folders))

    def test_folder_grouping(self):
        folders = self.account.folders
        # If you get errors here, you probably need to fill out [folder class].LOCALIZED_NAMES for your locale.
        self.assertEqual(len(folders[Inbox]), 1)
        self.assertEqual(len(folders[SentItems]), 1)
        self.assertEqual(len(folders[Outbox]), 1)
        self.assertEqual(len(folders[DeletedItems]), 1)
        self.assertEqual(len(folders[JunkEmail]), 1)
        self.assertEqual(len(folders[Drafts]), 1)
        self.assertGreaterEqual(len(folders[Contacts]), 1)
        self.assertGreaterEqual(len(folders[Calendar]), 1)
        self.assertGreaterEqual(len(folders[Tasks]), 1)
        for f in folders[Messages]:
            self.assertEqual(f.folder_class, 'IPF.Note')
        for f in folders[Contacts]:
            self.assertEqual(f.folder_class, 'IPF.Contact')
        for f in folders[Calendar]:
            self.assertEqual(f.folder_class, 'IPF.Appointment')
        for f in folders[Tasks]:
            self.assertEqual(f.folder_class, 'IPF.Task')

    def test_get_folder_by_name(self):
        folder_name = Calendar.LOCALIZED_NAMES[self.account.locale][0]
        f = self.account.root.get_folder_by_name(folder_name)
        self.assertEqual(f.name, folder_name)

    def test_counts(self):
        # Test count values on a folder
        # TODO: Subfolder creation isn't supported yet, so we can't test that child_folder_count changes
        self.assertGreaterEqual(self.account.inbox.total_count, 0)
        if self.account.inbox.unread_count is not None:
            self.assertGreaterEqual(self.account.inbox.unread_count, 0)
        self.assertGreaterEqual(self.account.inbox.child_folder_count, 0)
        # Create some items
        items = []
        for i in range(3):
            subject = 'Test Subject %s' % i
            item = Message(account=self.account, folder=self.account.inbox, is_read=False, subject=subject,
                           categories=self.categories)
            item.save()
            items.append(item)
        # Refresh values
        self.account.inbox.refresh()
        self.assertGreaterEqual(self.account.inbox.total_count, 3)
        self.assertGreaterEqual(self.account.inbox.unread_count, 3)
        self.assertGreaterEqual(self.account.inbox.child_folder_count, 0)
        for i in items:
            i.is_read = True
            i.save()
        # Refresh values and see that unread_count changes
        self.account.inbox.refresh()
        self.assertGreaterEqual(self.account.inbox.total_count, 3)
        if self.account.inbox.unread_count is not None:
            self.assertGreaterEqual(self.account.inbox.unread_count, 0)
        self.assertGreaterEqual(self.account.inbox.child_folder_count, 0)
        self.bulk_delete(items)

    def test_refresh(self):
        # Test that we can refresh folders
        folders = self.account.folders
        for folder_cls, cls_folders in folders.items():
            for f in cls_folders:
                old_values = {}
                for k in folder_cls.__slots__:
                    old_values[k] = getattr(f, k)
                    if k in ('account', 'folder_id', 'changekey'):
                        # These are needed for a successful refresh()
                        continue
                    setattr(f, k, get_random_string(16))
                f.refresh()
                for k in folder_cls.__slots__:
                    self.assertEqual(getattr(f, k), old_values[k])


class BaseItemTest(EWSTest):
    TEST_FOLDER = None
    ITEM_CLASS = None

    @classmethod
    def setUpClass(cls):
        if cls is BaseItemTest:
            raise unittest.SkipTest("Skip BaseItemTest, it's only for inheritance")
        super(BaseItemTest, cls).setUpClass()

    def setUp(self):
        super(BaseItemTest, self).setUp()
        self.test_folder = getattr(self.account, self.TEST_FOLDER)
        self.assertEqual(self.test_folder.DISTINGUISHED_FOLDER_ID, self.TEST_FOLDER)
        self.test_folder.filter(categories__contains=self.categories).delete()

    def tearDown(self):
        self.test_folder.filter(categories__contains=self.categories).delete()
        # Delete all delivery receipts
        self.test_folder.filter(subject__startswith='Delivered: Subject: ').delete()

    def get_random_insert_kwargs(self):
        insert_kwargs = {}
        for f in self.ITEM_CLASS.FIELDS:
            if f.is_read_only:
                # These cannot be created
                continue
            if f.name == 'attachments':
                # Testing attachments is heavy. Leave this to specific tests
                continue
            if f.name == 'resources':
                # The test server doesn't have any resources
                continue
            if f.name == 'optional_attendees':
                # 'optional_attendees' and 'required_attendees' are mutually exclusive
                insert_kwargs[f.name] = None
                continue
            if f.name == 'start':
                insert_kwargs[f.name], insert_kwargs['end'] = get_random_datetime_range()
                continue
            if f.name == 'end':
                continue
            if f.name == 'due_date':
                # start_date must be before due_date
                insert_kwargs['start_date'], insert_kwargs[f.name] = get_random_datetime_range()
                continue
            if f.name == 'start_date':
                continue
            if f.name == 'status':
                # Start with an incomplete task
                status = get_random_choice(f.choices - {Task.COMPLETED})
                insert_kwargs[f.name] = status
                insert_kwargs['percent_complete'] = Decimal(0) if status == Task.NOT_STARTED else get_random_decimal(1, 99)
                continue
            if f.name == 'percent_complete':
                continue
            insert_kwargs[f.name] = self.random_val(f)
        return insert_kwargs

    def get_random_update_kwargs(self, item, insert_kwargs):
        update_kwargs = {}
        now = UTC_NOW()
        for f in self.ITEM_CLASS.FIELDS:
            if f.is_read_only:
                # These cannot be changed
                continue
            if not item.is_draft and f.is_read_only_after_send:
                # These cannot be changed when the item is no longer a draft
                continue
            if f.name == 'resources':
                # The test server doesn't have any resources
                continue
            if f.name == 'attachments':
                # Attachments are handled separately
                continue
            if f.name == 'start':
                update_kwargs[f.name], update_kwargs['end'] = get_random_datetime_range()
                continue
            if f.name == 'end':
                continue
            if f.name == 'due_date':
                # start_date must be before due_date, and before complete_date which must be in the past
                update_kwargs['start_date'], update_kwargs[f.name] = get_random_datetime_range(end_date=now)
                continue
            if f.name == 'start_date':
                continue
            if f.name == 'status':
                # Update task to a completed state. complete_date must be a date in the past, and < than start_date
                update_kwargs[f.name] = Task.COMPLETED
                update_kwargs['percent_complete'] = Decimal(100)
                continue
            if f.name == 'percent_complete':
                continue
            if f.name == 'reminder_is_set':
                if self.ITEM_CLASS == Task:
                    # Task type doesn't allow updating 'reminder_is_set' to True
                    update_kwargs[f.name] = False
                else:
                    update_kwargs[f.name] = not insert_kwargs[f.name]
                continue
            if f.value_cls == bool:
                update_kwargs[f.name] = not insert_kwargs[f.name]
                continue
            if f.value_cls == Choice:
                update_kwargs[f.name] = get_random_choice([v for v in f.choices if v!= insert_kwargs[f.name]])
                continue
            if f.value_cls in (Mailbox, Attendee):
                if insert_kwargs[f.name] is None:
                    update_kwargs[f.name] = self.random_val(f)
                else:
                    update_kwargs[f.name] = None
                continue
            update_kwargs[f.name] = self.random_val(f)
        if update_kwargs.get('is_all_day', False):
            # For is_all_day items, EWS will remove the time part of start and end values
            update_kwargs['start'] = update_kwargs['start'].replace(hour=0, minute=0, second=0, microsecond=0)
            update_kwargs['end'] = update_kwargs['end'].replace(hour=0, minute=0, second=0, microsecond=0)
        if self.ITEM_CLASS == CalendarItem:
            # EWS always sets due date to 'start'
            update_kwargs['reminder_due_by'] = update_kwargs['start']
        return update_kwargs

    def get_test_item(self, folder=None, categories=None):
        item_kwargs = self.get_random_insert_kwargs()
        item_kwargs['categories'] = categories or self.categories
        return self.ITEM_CLASS(account=self.account, folder=folder or self.test_folder, **item_kwargs)

    def test_field_names(self):
        # Test that fieldnames don't clash with Python keywords
        for f in self.ITEM_CLASS.FIELDS:
            self.assertNotIn(f.name, kwlist)

    def test_magic(self):
        item = self.get_test_item()
        self.assertIn('item_id', str(item))
        self.assertIn(item.__class__.__name__, repr(item))

    def test_validation(self):
        item = self.get_test_item()
        item.clean()
        for f in self.ITEM_CLASS.FIELDS:
            # Test field maxlength
            if isinstance(f, TextField) and f.max_length:
                with self.assertRaises(ValueError):
                    setattr(item, f.name, 'a' * (f.max_length + 1))
                    item.clean()
                    setattr(item, f.name, 'a')

    def test_empty_args(self):
        # We allow empty sequences for these methods
        self.assertEqual(self.test_folder.bulk_create(items=[]), [])
        self.assertEqual(list(self.account.fetch(ids=[])), [])
        self.assertEqual(self.account.bulk_create(folder=self.test_folder, items=[]), [])
        self.assertEqual(self.account.bulk_update(items=[]), [])
        self.assertEqual(self.account.bulk_delete(ids=[]), [])
        self.assertEqual(self.account.bulk_send(ids=[]), [])
        self.assertEqual(self.account.bulk_move(ids=[], to_folder=self.account.trash), [])
        self.assertEqual(self.account.upload(data=[]), [])
        self.assertEqual(self.account.export(items=[]), [])

    def test_qs_args(self):
        # We allow querysets for these methods
        qs = self.test_folder.none()
        self.assertEqual(list(self.account.fetch(ids=qs)), [])
        with self.assertRaises(ValueError):
            # bulk_update does not allow queryset input
            self.assertEqual(self.account.bulk_update(items=qs), [])
        self.assertEqual(self.account.bulk_delete(ids=qs), [])
        self.assertEqual(self.account.bulk_send(ids=qs), [])
        self.assertEqual(self.account.bulk_move(ids=qs, to_folder=self.account.trash), [])
        with self.assertRaises(ValueError):
            self.assertEqual(self.account.upload(data=qs), [])
        with self.assertRaises(ValueError):
            self.assertEqual(self.account.export(items=qs), [])

    def test_no_kwargs(self):
        self.assertEqual(self.test_folder.bulk_create([]), [])
        self.assertEqual(list(self.account.fetch([])), [])
        self.assertEqual(self.account.bulk_create(self.test_folder, []), [])
        self.assertEqual(self.account.bulk_update([]), [])
        self.assertEqual(self.account.bulk_delete([]), [])
        self.assertEqual(self.account.bulk_send([]), [])
        self.assertEqual(self.account.bulk_move([], to_folder=self.account.trash), [])
        self.assertEqual(self.account.upload([]), [])
        self.assertEqual(self.account.export([]), [])

    def test_invalid_bulk_args(self):
        # Test bulk_create
        with self.assertRaises(ValueError):
            # Folder must belong to account
            self.account.bulk_create(folder=Folder(account=None), items=[])
        with self.assertRaises(AttributeError):
            # Must have folder on save
            self.account.bulk_create(folder=None, items=[], message_disposition=SAVE_ONLY)
        # Test that we can send_and_save with a default folder
        self.account.bulk_create(folder=None, items=[], message_disposition=SEND_AND_SAVE_COPY)
        with self.assertRaises(AttributeError):
            # Must not have folder on send-only
            self.account.bulk_create(folder=self.test_folder, items=[], message_disposition=SEND_ONLY)

        # Test bulk_update
        with self.assertRaises(ValueError):
            # Cannot update in send-only mode
            self.account.bulk_update(items=[], message_disposition=SEND_ONLY)

    def test_invalid_direct_args(self):
        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.account = None
            item.save()  # Must have account on save
        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.item_id = 'XXX'  # Fake a saved item
            item.account = None
            item.save()  # Must have account on update
        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.save(update_fields=['foo', 'bar'])  # update_fields is only valid on update

        if self.ITEM_CLASS == Message:
            with self.assertRaises(ValueError):
                item = self.get_test_item()
                item.account = None
                item.send()  # Must have account on send
            with self.assertRaises(ErrorItemNotFound):
                item = self.get_test_item()
                item.save()
                item_id, changekey = item.item_id, item.changekey
                item.delete()
                item.item_id, item.changekey = item_id, changekey
                item.send()  # Item disappeared
            with self.assertRaises(AttributeError):
                item = self.get_test_item()
                item.send(copy_to_folder=self.account.trash, save_copy=False)  # Inconsistent args

        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.account = None
            item.refresh()  # Must have account on refresh
        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.refresh()  # Refresh an item that has not been saved
        with self.assertRaises(ErrorItemNotFound):
            item = self.get_test_item()
            item.save()
            item_id, changekey = item.item_id, item.changekey
            item.delete(affected_task_occurrences=ALL_OCCURRENCIES)
            item.item_id, item.changekey = item_id, changekey
            item.refresh()  # Refresh an item that doesn't exist

        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.account = None
            item.move(to_folder=self.test_folder)  # Must have an account on move
        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.move(to_folder=self.test_folder)  # Must be an existing item
        with self.assertRaises(ErrorItemNotFound):
            item = self.get_test_item()
            item.save()
            item_id, changekey = item.item_id, item.changekey
            item.delete(affected_task_occurrences=ALL_OCCURRENCIES)
            item.item_id, item.changekey = item_id, changekey
            item.move(to_folder=self.test_folder)  # Item disappeared

        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.account = None
            item.delete()  # Must have an account
        with self.assertRaises(ValueError):
            item = self.get_test_item()
            item.delete(affected_task_occurrences=ALL_OCCURRENCIES)  # Must be an existing item
        with self.assertRaises(ErrorItemNotFound):
            item = self.get_test_item()
            item.save()
            item_id, changekey = item.item_id, item.changekey
            item.delete(affected_task_occurrences=ALL_OCCURRENCIES)
            item.item_id, item.changekey = item_id, changekey
            item.delete(affected_task_occurrences=ALL_OCCURRENCIES)  # Item disappeared

    def test_querysets(self):
        test_items = []
        for i in range(4):
            item = self.get_test_item()
            item.subject = 'Item %s' % i
            item.save()
            test_items.append(item)
        qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
        test_cat = self.categories[0]
        self.assertEqual(
            set((i.subject, i.categories[0]) for i in qs),
            {('Item 0', test_cat), ('Item 1', test_cat), ('Item 2', test_cat), ('Item 3', test_cat)}
        )
        self.assertEqual(
            [(i.subject, i.categories[0]) for i in qs.none()],
            []
        )
        self.assertEqual(
            [(i.subject, i.categories[0]) for i in qs.filter(subject__startswith='Item 2')],
            [('Item 2', test_cat)]
        )
        self.assertEqual(
            set((i.subject, i.categories[0]) for i in qs.exclude(subject__startswith='Item 2')),
            {('Item 0', test_cat), ('Item 1', test_cat), ('Item 3', test_cat)}
        )
        self.assertEqual(
            set((i.subject, i.categories) for i in qs.only('subject')),
            {('Item 0', None), ('Item 1', None), ('Item 2', None), ('Item 3', None)}
        )
        self.assertEqual(
            [(i.subject, i.categories[0]) for i in qs.order_by('subject')],
            [('Item 0', test_cat), ('Item 1', test_cat), ('Item 2', test_cat), ('Item 3', test_cat)]
        )
        self.assertEqual(  # Test '-some_field' syntax for reverse sorting
            [(i.subject, i.categories[0]) for i in qs.order_by('-subject')],
            [('Item 3', test_cat), ('Item 2', test_cat), ('Item 1', test_cat), ('Item 0', test_cat)]
        )
        self.assertEqual(  # Test ordering on a field that we don't need to fetch
            [(i.subject, i.categories[0]) for i in qs.order_by('-subject').only('categories')],
            [(None, test_cat), (None, test_cat), (None, test_cat), (None, test_cat)]
        )
        self.assertEqual(
            [(i.subject, i.categories[0]) for i in qs.order_by('subject').reverse()],
            [('Item 3', test_cat), ('Item 2', test_cat), ('Item 1', test_cat), ('Item 0', test_cat)]
        )
        with self.assertRaises(ValueError):
            list(qs.values([]))
        self.assertEqual(
            [i for i in qs.order_by('subject').values('subject')],
            [{'subject': 'Item 0'}, {'subject': 'Item 1'}, {'subject': 'Item 2'}, {'subject': 'Item 3'}]
        )
        self.assertEqual(
            list(qs.order_by('subject').values('item_id')),
            [{'item_id': i.item_id} for i in test_items]
        )
        self.assertEqual(
            list(qs.order_by('subject').values('changekey')),
            [{'changekey': i.changekey} for i in test_items]
        )
        self.assertEqual(
            list(qs.order_by('subject').values('item_id', 'changekey')),
            [{k: getattr(i, k) for k in ('item_id', 'changekey')} for i in test_items]
        )
        self.assertEqual(
            set(i for i in qs.values_list('subject')),
            {('Item 0',), ('Item 1',), ('Item 2',), ('Item 3',)}
        )
        self.assertEqual(
            list(qs.order_by('subject').values_list('item_id')),
            [(i.item_id,) for i in test_items]
        )
        self.assertEqual(
            list(qs.order_by('subject').values_list('changekey')),
            [(i.changekey,) for i in test_items]
        )
        self.assertEqual(
            list(qs.order_by('subject').values_list('item_id', 'changekey')),
            [(i.item_id, i.changekey) for i in test_items]
        )
        with self.assertRaises(ValueError):
            list(qs.values_list('item_id', 'changekey', flat=True))
        self.assertEqual(
            list(qs.order_by('subject').values_list('item_id', flat=True)),
            [i.item_id for i in test_items]
        )
        self.assertEqual(
            list(qs.order_by('subject').values_list('changekey', flat=True)),
            [i.changekey for i in test_items]
        )
        self.assertEqual(
            set(i for i in qs.values_list('subject', flat=True)),
            {'Item 0', 'Item 1', 'Item 2', 'Item 3'}
        )
        self.assertEqual(
            qs.values_list('subject', flat=True).get(subject='Item 2'),
            'Item 2'
        )
        self.assertEqual(
            set((i.subject, i.categories[0]) for i in qs.exclude(subject__startswith='Item 2')),
            {('Item 0', test_cat), ('Item 1', test_cat), ('Item 3', test_cat)}
        )
        # Test that we can sort on a field that we don't want
        self.assertEqual(
            [i.categories[0] for i in qs.only('categories').order_by('subject')],
            [test_cat, test_cat, test_cat, test_cat]
        )
        self.assertEqual(
            set((i.subject, i.categories[0]) for i in qs.iterator()),
            {('Item 0', test_cat), ('Item 1', test_cat), ('Item 2', test_cat), ('Item 3', test_cat)}
        )
        self.assertEqual(qs.get(subject='Item 3').subject, 'Item 3')
        with self.assertRaises(DoesNotExist):
            qs.get(subject='Item XXX')
        with self.assertRaises(MultipleObjectsReturned):
            qs.get(subject__startswith='Item')
        # len() and count()
        self.assertEqual(len(qs), 4)
        self.assertEqual(qs.count(), 4)
        # Indexing and slicing
        self.assertTrue(isinstance(qs[0], self.ITEM_CLASS))
        self.assertEqual(len(list(qs[1:3])), 2)
        self.assertEqual(len(qs), 4)
        with self.assertRaises(IndexError):
            foo = qs[99999]
        # Exists
        self.assertEqual(qs.exists(), True)
        self.assertEqual(qs.filter(subject='Test XXX').exists(), False)
        self.assertEqual(
            qs.filter(subject__startswith='Item').delete(),
            [True, True, True, True]
        )

    def test_queryset_failure(self):
        qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
        with self.assertRaises(ValueError):
            qs.order_by('XXX')
        with self.assertRaises(ValueError):
            qs.values('XXX')
        with self.assertRaises(ValueError):
            qs.values_list('XXX')
        with self.assertRaises(ValueError):
            qs.only('XXX')
        with self.assertRaises(ValueError):
            qs.reverse()  # We can't reverse when we haven't defined an order yet

    def test_order_by_failure(self):
        # Test error handling on indexed properties with labels and subfields
        if self.ITEM_CLASS == Contact:
            qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
            with self.assertRaises(ValueError):
                qs.order_by('email_addresses')  # Must have label
            with self.assertRaises(ValueError):
                qs.order_by('email_addresses__FOO')  # Must have a valid label
            with self.assertRaises(ValueError):
                qs.order_by('email_addresses__EmailAddress1__FOO')  # Must not have a subfield
            with self.assertRaises(ValueError):
                qs.order_by('physical_addresses__Business')  # Must have a subfield
            with self.assertRaises(ValueError):
                qs.order_by('physical_addresses__Business__FOO')  # Must have a valid subfield

    def test_order_by(self):
        # Test order_by() on normal field
        test_items = []
        for i in range(4):
            item = self.get_test_item()
            item.subject = 'Subj %s' % i
            test_items.append(item)
        self.test_folder.bulk_create(items=test_items)
        qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
        self.assertEqual(
            [i for i in qs.order_by('subject').values_list('subject', flat=True)],
            ['Subj 0', 'Subj 1', 'Subj 2', 'Subj 3']
        )
        self.assertEqual(
            [i for i in qs.order_by('-subject').values_list('subject', flat=True)],
            ['Subj 3', 'Subj 2', 'Subj 1', 'Subj 0']
        )
        self.bulk_delete(qs)

        # Test order_by() on ExtendedProperty
        test_items = []
        for i in range(4):
            item = self.get_test_item()
            item.extern_id = 'ID %s' % i
            test_items.append(item)
        self.test_folder.bulk_create(items=test_items)
        qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
        self.assertEqual(
            [i for i in qs.order_by('extern_id').values_list('extern_id', flat=True)],
            ['ID 0', 'ID 1', 'ID 2', 'ID 3']
        )
        self.assertEqual(
            [i for i in qs.order_by('-extern_id').values_list('extern_id', flat=True)],
            ['ID 3', 'ID 2', 'ID 1', 'ID 0']
        )
        self.bulk_delete(qs)

        # Test order_by() on IndexedField (simple and multi-subfield). Only Contact items have these
        if self.ITEM_CLASS == Contact:
            test_items = []
            label = random.choice(list(EmailAddress.LABELS))
            for i in range(4):
                item = self.get_test_item()
                item.email_addresses = [EmailAddress(email='%s@foo.com' % i, label=label)]
                test_items.append(item)
            self.test_folder.bulk_create(items=test_items)
            qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
            self.assertEqual(
                [i[0].email for i in qs.order_by('email_addresses__%s' % label).values_list('email_addresses', flat=True)],
                ['0@foo.com', '1@foo.com', '2@foo.com', '3@foo.com']
            )
            self.assertEqual(
                [i[0].email for i in qs.order_by('-email_addresses__%s' % label).values_list('email_addresses', flat=True)],
                ['3@foo.com', '2@foo.com', '1@foo.com', '0@foo.com']
            )
            self.bulk_delete(qs)

            test_items = []
            label = random.choice(list(PhysicalAddress.LABELS))
            for i in range(4):
                item = self.get_test_item()
                item.physical_addresses = [PhysicalAddress(street='Elm St %s' % i, label=label)]
                test_items.append(item)
            self.test_folder.bulk_create(items=test_items)
            qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
            self.assertEqual(
                [i[0].street for i in qs.order_by('physical_addresses__%s__street' % label).values_list('physical_addresses', flat=True)],
                ['Elm St 0', 'Elm St 1', 'Elm St 2', 'Elm St 3']
            )
            self.assertEqual(
                [i[0].street for i in qs.order_by('-physical_addresses__%s__street' % label).values_list('physical_addresses', flat=True)],
                ['Elm St 3', 'Elm St 2', 'Elm St 1', 'Elm St 0']
            )
            self.bulk_delete(qs)

        # Test sorting on multiple fields
        test_items = []
        for i in range(2):
            for j in range(2):
                item = self.get_test_item()
                item.subject = 'Subj %s' % i
                item.extern_id = 'ID %s' % j
                test_items.append(item)
        self.test_folder.bulk_create(items=test_items)
        qs = QuerySet(self.test_folder).filter(categories__contains=self.categories)
        self.assertEqual(
            [i for i in qs.order_by('subject', 'extern_id').values('subject', 'extern_id')],
            [{'subject': 'Subj 0', 'extern_id': 'ID 0'},
             {'subject': 'Subj 0', 'extern_id': 'ID 1'},
             {'subject': 'Subj 1', 'extern_id': 'ID 0'},
             {'subject': 'Subj 1', 'extern_id': 'ID 1'}]
        )
        self.assertEqual(
            [i for i in qs.order_by('-subject', 'extern_id').values('subject', 'extern_id')],
            [{'subject': 'Subj 1', 'extern_id': 'ID 0'},
             {'subject': 'Subj 1', 'extern_id': 'ID 1'},
             {'subject': 'Subj 0', 'extern_id': 'ID 0'},
             {'subject': 'Subj 0', 'extern_id': 'ID 1'}]
        )
        self.assertEqual(
            [i for i in qs.order_by('subject', '-extern_id').values('subject', 'extern_id')],
            [{'subject': 'Subj 0', 'extern_id': 'ID 1'},
             {'subject': 'Subj 0', 'extern_id': 'ID 0'},
             {'subject': 'Subj 1', 'extern_id': 'ID 1'},
             {'subject': 'Subj 1', 'extern_id': 'ID 0'}]
        )
        self.assertEqual(
            [i for i in qs.order_by('-subject', '-extern_id').values('subject', 'extern_id')],
            [{'subject': 'Subj 1', 'extern_id': 'ID 1'},
             {'subject': 'Subj 1', 'extern_id': 'ID 0'},
             {'subject': 'Subj 0', 'extern_id': 'ID 1'},
             {'subject': 'Subj 0', 'extern_id': 'ID 0'}]
        )
        # Test sorting on a field that we don't need
        self.assertEqual(
            [(i.subject, i.extern_id) for i in qs.order_by('subject', 'extern_id').only('subject')],
            [('Subj 0', None),
             ('Subj 0', None),
             ('Subj 1', None),
             ('Subj 1', None)]
        )
        self.bulk_delete(qs)

    def test_finditems(self):
        now = UTC_NOW()

        # Test argument types
        item = self.get_test_item()
        ids = self.test_folder.bulk_create(items=[item])
        # No arguments. There may be leftover items in the folder, so just make sure there's at least one.
        self.assertGreaterEqual(
            len(self.test_folder.filter()),
            1
        )
        # Q object
        self.assertEqual(
            len(self.test_folder.filter(Q(subject=item.subject))),
            1
        )
        # Multiple Q objects
        self.assertEqual(
            len(self.test_folder.filter(Q(subject=item.subject), ~Q(subject=item.subject + 'XXX'))),
            1
        )
        # Multiple Q object and kwargs
        self.assertEqual(
            len(self.test_folder.filter(Q(subject=item.subject), categories__contains=item.categories)),
            1
        )
        self.bulk_delete(ids)

        # Test categories which are handled specially - only '__contains' and '__in' lookups are supported
        item = self.get_test_item(categories=['TestA', 'TestB'])
        ids = self.test_folder.bulk_create(items=[item])
        common_qs = self.test_folder.filter(subject=item.subject)  # Guard against other sumultaneous runs
        with self.assertRaises(ErrorContainsFilterWrongType):
            len(common_qs.filter(categories__contains='ci6xahH1'))  # Plain string is not supported
        self.assertEqual(
            len(common_qs.filter(categories__contains=['ci6xahH1'])),  # Same, but as list
            0
        )
        self.assertEqual(
            len(common_qs.filter(categories__contains=['TestA', 'TestC'])),  # One wrong category
            0
        )
        self.assertEqual(
            len(common_qs.filter(categories__contains=['TESTA'])),  # Test case insensitivity
            1
        )
        self.assertEqual(
            len(common_qs.filter(categories__contains=['testa'])),  # Test case insensitivity
            1
        )
        self.assertEqual(
            len(common_qs.filter(categories__contains=['TestA'])),  # Partial
            1
        )
        self.assertEqual(
            len(common_qs.filter(categories__contains=item.categories)),  # Exact match
            1
        )
        with self.assertRaises(ValueError):
            len(common_qs.filter(categories__in='ci6xahH1'))  # Plain string is not supported
        self.assertEqual(
            len(common_qs.filter(categories__in=['ci6xahH1'])),  # Same, but as list
            0
        )
        self.assertEqual(
            len(common_qs.filter(categories__in=['TestA', 'TestC'])),  # One wrong category
            1
        )
        self.assertEqual(
            len(common_qs.filter(categories__in=['TestA'])),  # Partial
            1
        )
        self.assertEqual(
            len(common_qs.filter(categories__in=item.categories)),  # Exact match
            1
        )
        self.bulk_delete(ids)

        common_qs = self.test_folder.filter(categories__contains=self.categories)
        one_hour = datetime.timedelta(hours=1)
        two_hours = datetime.timedelta(hours=2)
        # Test 'range'
        ids = self.test_folder.bulk_create(items=[self.get_test_item()])
        self.assertEqual(
            len(common_qs.filter(datetime_created__range=(now + one_hour, now + two_hours))),
            0
        )
        self.assertEqual(
            len(common_qs.filter(datetime_created__range=(now - one_hour, now + one_hour))),
            1
        )
        self.bulk_delete(ids)

        # Test '>'
        ids = self.test_folder.bulk_create(items=[self.get_test_item()])
        self.assertEqual(
            len(common_qs.filter(datetime_created__gt=now + one_hour)),
            0
        )
        self.assertEqual(
            len(common_qs.filter(datetime_created__gt=now - one_hour)),
            1
        )
        self.bulk_delete(ids)

        # Test '>='
        ids = self.test_folder.bulk_create(items=[self.get_test_item()])
        self.assertEqual(
            len(common_qs.filter(datetime_created__gte=now + one_hour)),
            0
        )
        self.assertEqual(
            len(common_qs.filter(datetime_created__gte=now - one_hour)),
            1
        )
        self.bulk_delete(ids)

        # Test '<'
        ids = self.test_folder.bulk_create(items=[self.get_test_item()])
        self.assertEqual(
            len(common_qs.filter(datetime_created__lt=now - one_hour)),
            0
        )
        self.assertEqual(
            len(common_qs.filter(datetime_created__lt=now + one_hour)),
            1
        )
        self.bulk_delete(ids)

        # Test '<='
        ids = self.test_folder.bulk_create(items=[self.get_test_item()])
        self.assertEqual(
            len(common_qs.filter(datetime_created__lte=now - one_hour)),
            0
        )
        self.assertEqual(
            len(common_qs.filter(datetime_created__lte=now + one_hour)),
            1
        )
        self.bulk_delete(ids)

        # Test '='
        item = self.get_test_item()
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject=item.subject + 'XXX')),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject=item.subject)),
            1
        )
        self.bulk_delete(ids)

        # Test '!='
        item = self.get_test_item()
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__not=item.subject)),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__not=item.subject + 'XXX')),
            1
        )
        self.bulk_delete(ids)

        # Test 'exact'
        item = self.get_test_item()
        item.subject = 'aA' + item.subject[2:]
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__exact=item.subject + 'XXX')),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__exact=item.subject.lower())),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__exact=item.subject.upper())),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__exact=item.subject)),
            1
        )
        self.bulk_delete(ids)

        # Test 'iexact'
        item = self.get_test_item()
        item.subject = 'aA' + item.subject[2:]
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__iexact=item.subject + 'XXX')),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__iexact=item.subject.lower())),
            1
        )
        self.assertEqual(
            len(common_qs.filter(subject__iexact=item.subject.upper())),
            1
        )
        self.assertEqual(
            len(common_qs.filter(subject__iexact=item.subject)),
            1
        )
        self.bulk_delete(ids)

        # Test 'contains'
        item = self.get_test_item()
        item.subject = item.subject[2:8] + 'aA' + item.subject[8:]
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__contains=item.subject[2:14] + 'XXX')),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__contains=item.subject[2:14].lower())),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__contains=item.subject[2:14].upper())),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__contains=item.subject[2:14])),
            1
        )
        self.bulk_delete(ids)

        # Test 'icontains'
        item = self.get_test_item()
        item.subject = item.subject[2:8] + 'aA' + item.subject[8:]
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__icontains=item.subject[2:14] + 'XXX')),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__icontains=item.subject[2:14].lower())),
            1
        )
        self.assertEqual(
            len(common_qs.filter(subject__icontains=item.subject[2:14].upper())),
            1
        )
        self.assertEqual(
            len(common_qs.filter(subject__icontains=item.subject[2:14])),
            1
        )
        self.bulk_delete(ids)

        # Test 'startswith'
        item = self.get_test_item()
        item.subject = 'aA' + item.subject[2:]
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__startswith='XXX' + item.subject[:12])),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__startswith=item.subject[:12].lower())),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__startswith=item.subject[:12].upper())),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__startswith=item.subject[:12])),
            1
        )
        self.bulk_delete(ids)

        # Test 'istartswith'
        item = self.get_test_item()
        item.subject = 'aA' + item.subject[2:]
        ids = self.test_folder.bulk_create(items=[item])
        self.assertEqual(
            len(common_qs.filter(subject__istartswith='XXX' + item.subject[:12])),
            0
        )
        self.assertEqual(
            len(common_qs.filter(subject__istartswith=item.subject[:12].lower())),
            1
        )
        self.assertEqual(
            len(common_qs.filter(subject__istartswith=item.subject[:12].upper())),
            1
        )
        self.assertEqual(
            len(common_qs.filter(subject__istartswith=item.subject[:12])),
            1
        )
        self.bulk_delete(ids)

    def test_filter_on_all_fields(self):
        # Test that we can filter on all field names that we support filtering on
        # TODO: Test filtering on subfields of IndexedField
        item = self.get_test_item()
        if hasattr(item, 'is_all_day'):
            item.is_all_day = False  # Make sure start- and end dates don't change
        ids = self.test_folder.bulk_create(items=[item])
        common_qs = self.test_folder.filter(categories__contains=self.categories)
        for f in self.ITEM_CLASS.FIELDS:
            if f.name in ('status', 'companies', 'reminder_due_by'):
                # For some reason, EWS disallows searching on these, instead throwing ErrorInvalidValueForProperty
                continue
            if f.name in ('percent_complete',):
                # This simply doesn't match anything. Error in EWS?
                continue
            if f.is_list and f.value_cls in (Attachment, Mailbox, Attendee, PhysicalAddress):
                # These are not searchable, either. Maybe EWS just doesn't support it
                continue
            val = getattr(item, f.name)
            if val is None:
                # We cannot filter on None values
                continue
            if f.is_list:
                # Filter multi-value fields with __in and __contains
                filter_kwargs = [{'%s__in' % f.name: val}, {'%s__contains' % f.name: val}]
            else:
                # Filter all others with =, __in and __contains. We could have more filters here, but these should
                # always match.
                filter_kwargs = [{f.name: val}, {'%s__in' % f.name: [val]}]
                if f.value_cls in string_types and not isinstance(f, ChoiceField) and f.name not in ('display_name'):
                    # Choice fields cannot be filtered using __contains
                    # For some reason, 'display_name__contains' does not match. Error in EWS?
                    filter_kwargs.append({'%s__contains' % f.name: val})
            for kw in filter_kwargs:
                self.assertEqual(len(common_qs.filter(**kw)), 1)
        self.bulk_delete(ids)

    def test_paging(self):
        # Test that paging services work correctly. Default EWS paging size is 1000 items. Our default is 100 items.
        items = []
        for _ in range(11):
            i = self.get_test_item()
            del i.attachments[:]
            items.append(i)
        self.test_folder.bulk_create(items=items)
        ids = self.test_folder.filter(categories__contains=self.categories).values_list('item_id', 'changekey')
        self.bulk_delete(ids.iterator(page_size=10))

    def test_slicing(self):
        # Test that slicing works correctly
        items = []
        for i in range(4):
            item = self.get_test_item()
            item.subject = 'Subj %s' % i
            del item.attachments[:]
            items.append(item)
        ids = self.test_folder.bulk_create(items=items)
        qs = self.test_folder.filter(categories__contains=self.categories).only('subject').order_by('subject')

        # Test positive index
        self.assertEqual(
            qs.copy()[0].subject,
            'Subj 0'
        )
        # Test positive index
        self.assertEqual(
            qs.copy()[3].subject,
            'Subj 3'
        )
        # Test negative index
        self.assertEqual(
            qs.copy()[-2].subject,
            'Subj 2'
        )
        # Test positive slice
        self.assertEqual(
            [i.subject for i in qs.copy()[0:2]],
            ['Subj 0', 'Subj 1']
        )
        # Test positive slice
        self.assertEqual(
            [i.subject for i in qs.copy()[2:4]],
            ['Subj 2', 'Subj 3']
        )
        # Test positive open slice
        self.assertEqual(
            [i.subject for i in qs.copy()[:2]],
            ['Subj 0', 'Subj 1']
        )
        # Test positive open slice
        self.assertEqual(
            [i.subject for i in qs.copy()[2:]],
            ['Subj 2', 'Subj 3']
        )
        # Test negative slice
        self.assertEqual(
            [i.subject for i in qs.copy()[-3:-1]],
            ['Subj 1', 'Subj 2']
        )
        # Test negative slice
        self.assertEqual(
            [i.subject for i in qs.copy()[1:-1]],
            ['Subj 1', 'Subj 2']
        )
        # Test negative open slice
        self.assertEqual(
            [i.subject for i in qs.copy()[:-2]],
            ['Subj 0', 'Subj 1']
        )
        # Test negative open slice
        self.assertEqual(
            [i.subject for i in qs.copy()[-2:]],
            ['Subj 2', 'Subj 3']
        )
        # Test positive slice with step
        self.assertEqual(
            [i.subject for i in qs.copy()[0:4:2]],
            ['Subj 0', 'Subj 2']
        )
        # Test negative slice with step
        self.assertEqual(
            [i.subject for i in qs.copy()[4:0:-2]],
            ['Subj 3', 'Subj 1']
        )
        self.bulk_delete(ids)

    def test_getitems(self):
        item = self.get_test_item()
        self.test_folder.bulk_create(items=[item, item])
        ids = self.test_folder.filter(categories__contains=item.categories)
        items = list(self.account.fetch(ids=ids))
        for item in items:
            assert isinstance(item, self.ITEM_CLASS)
        self.assertEqual(len(items), 2)
        self.bulk_delete(ids)

    def test_only_fields(self):
        item = self.get_test_item()
        self.test_folder.bulk_create(items=[item, item])
        items = self.test_folder.filter(categories__contains=item.categories)
        for item in items:
            assert isinstance(item, self.ITEM_CLASS)
            for f in self.ITEM_CLASS.FIELDS:
                self.assertTrue(hasattr(item, f.name))
                if f.name in ('optional_attendees', 'required_attendees', 'resources'):
                    continue
                elif f.is_read_only:
                    continue
                self.assertIsNotNone(getattr(item, f.name), (f, getattr(item, f.name)))
        self.assertEqual(len(items), 2)
        only_fields = ('subject', 'body', 'categories')
        items = self.test_folder.filter(categories__contains=item.categories).only(*only_fields)
        for item in items:
            assert isinstance(item, self.ITEM_CLASS)
            for f in self.ITEM_CLASS.FIELDS:
                self.assertTrue(hasattr(item, f.name))
                if f.name in only_fields:
                    self.assertIsNotNone(getattr(item, f.name), (f.name, getattr(item, f.name)))
                elif f.is_required:
                    v = getattr(item, f.name)
                    if f.name == 'attachments':
                        self.assertEqual(v, [], (f.name, v))
                    elif f.default is None:
                        self.assertIsNone(v, (f.name, v))
                    else:
                        self.assertEqual(v, f.default, (f.name, v))
        self.assertEqual(len(items), 2)
        self.bulk_delete(items)

    def test_save_and_delete(self):
        # Test that we can create, update and delete single items using methods directly on the item.
        # For CalendarItem instances, the 'is_all_day' attribute affects the 'start' and 'end' values. Changing from
        # 'false' to 'true' removes the time part of these datetimes.
        insert_kwargs = self.get_random_insert_kwargs()
        if 'is_all_day' in insert_kwargs:
            insert_kwargs['is_all_day'] = False
        item = self.ITEM_CLASS(account=self.account, folder=self.test_folder, **insert_kwargs)
        self.assertIsNone(item.item_id)
        self.assertIsNone(item.changekey)

        # Create
        item.save()
        self.assertIsNotNone(item.item_id)
        self.assertIsNotNone(item.changekey)
        for k, v in insert_kwargs.items():
            self.assertEqual(getattr(item, k), v, (k, getattr(item, k), v))
        # Test that whatever we have locally also matches whatever is in the DB
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        for f in item.FIELDS:
            old, new = getattr(item, f.name), getattr(fresh_item, f.name)
            if f.is_read_only and old is None:
                # Some fields are automatically set server-side
                continue
            if f.name == 'reminder_due_by':
                # EWS sets a default value if it is not set on insert. Ignore
                continue
            if f.is_list:
                old, new = set(old or ()), set(new or ())
            self.assertEqual(old, new, (f.name, old, new))

        # Update
        update_kwargs = self.get_random_update_kwargs(item=item, insert_kwargs=insert_kwargs)
        for k, v in update_kwargs.items():
            setattr(item, k, v)
        item.save()
        for k, v in update_kwargs.items():
            self.assertEqual(getattr(item, k), v, (k, getattr(item, k), v))
        # Test that whatever we have locally also matches whatever is in the DB
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        for f in item.FIELDS:
            old, new = getattr(item, f.name), getattr(fresh_item, f.name)
            if f.is_read_only and old is None:
                # Some fields are automatically updated server-side
                continue
            if f.is_list:
                old, new = set(old or ()), set(new or ())
            self.assertEqual(old, new, (f.name, old, new, update_kwargs.get(f.name)))

        # Hard delete
        item_id = (item.item_id, item.changekey)
        item.delete(affected_task_occurrences=ALL_OCCURRENCIES)
        for e in self.account.fetch(ids=[item_id]):
            # It's gone from the account
            self.assertIsInstance(e, ErrorItemNotFound)
        # Really gone, not just changed ItemId
        items = self.test_folder.filter(categories__contains=item.categories)
        self.assertEqual(len(items), 0)

    def test_save_with_update_fields(self):
        # Create a test item
        insert_kwargs = self.get_random_insert_kwargs()
        if 'is_all_day' in insert_kwargs:
            insert_kwargs['is_all_day'] = False
        item = self.ITEM_CLASS(account=self.account, folder=self.test_folder, **insert_kwargs)
        with self.assertRaises(ValueError):
            item.save(update_fields=['subject'])  # update_fields does not work on item creation
        item.save()
        item.subject = 'XXX'
        item.body = 'YYY'
        item.save(update_fields=['subject'])
        item.refresh()
        self.assertEqual(item.subject, 'XXX')
        self.assertNotEqual(item.body, 'YYY')
        self.bulk_delete([item])

    def test_soft_delete(self):
        # First, empty trash bin
        self.account.trash.filter(categories__contains=self.categories).delete()
        self.account.recoverable_deleted_items.filter(categories__contains=self.categories).delete()
        item = self.get_test_item().save()
        item_id = (item.item_id, item.changekey)
        # Soft delete
        item.soft_delete(affected_task_occurrences=ALL_OCCURRENCIES)
        for e in self.account.fetch(ids=[item_id]):
            # It's gone from the test folder
            self.assertIsInstance(e, ErrorItemNotFound)
        # Really gone, not just changed ItemId
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 0)
        self.assertEqual(len(self.account.trash.filter(categories__contains=item.categories)), 0)
        # But we can find it in the recoverable items folder
        self.assertEqual(len(self.account.recoverable_deleted_items.filter(categories__contains=item.categories)), 1)

    def test_move_to_trash(self):
        # First, empty trash bin
        self.account.trash.filter(categories__contains=self.categories).delete()
        item = self.get_test_item().save()
        item_id = (item.item_id, item.changekey)
        # Move to trash
        item.move_to_trash(affected_task_occurrences=ALL_OCCURRENCIES)
        for e in self.account.fetch(ids=[item_id]):
            # Not in the test folder anymore
            self.assertIsInstance(e, ErrorItemNotFound)
        # Really gone, not just changed ItemId
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 0)
        # Test that the item moved to trash
        item = self.account.trash.get(categories__contains=item.categories)
        moved_item = list(self.account.fetch(ids=[item]))[0]
        # The item was copied, so the ItemId has changed. Let's compare the subject instead
        self.assertEqual(item.subject, moved_item.subject)

    def test_move(self):
        # First, empty trash bin
        self.account.trash.filter(categories__contains=self.categories).delete()
        item = self.get_test_item().save()
        item_id = (item.item_id, item.changekey)
        # Move to trash. We use trash because it can contain all item types. This changes the ItemId
        item.move(to_folder=self.account.trash)
        for e in self.account.fetch(ids=[item_id]):
            # original item ID no longer exists
            self.assertIsInstance(e, ErrorItemNotFound)
        # Test that the item moved to trash
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 0)
        moved_item = self.account.trash.get(categories__contains=item.categories)
        self.assertEqual(item.item_id, moved_item.item_id)
        self.assertEqual(item.changekey, moved_item.changekey)

    def test_refresh(self):
        # Test that we can refresh items, and that refresh fails if the item no longer exists on the server
        item = self.get_test_item().save()
        orig_subject = item.subject
        item.subject = 'XXX'
        item.refresh()
        self.assertEqual(item.subject, orig_subject)
        item.delete(affected_task_occurrences=ALL_OCCURRENCIES)
        with self.assertRaises(ValueError):
            # Item no longer has an ID
            item.refresh()

    def test_item(self):
        # Test insert
        # For CalendarItem instances, the 'is_all_day' attribute affects the 'start' and 'end' values. Changing from
        # 'false' to 'true' removes the time part of these datetimes.
        insert_kwargs = self.get_random_insert_kwargs()
        if 'is_all_day' in insert_kwargs:
            insert_kwargs['is_all_day'] = False
        item = self.ITEM_CLASS(**insert_kwargs)
        # Test with generator as argument
        insert_ids = self.test_folder.bulk_create(items=(i for i in [item]))
        self.assertEqual(len(insert_ids), 1)
        assert isinstance(insert_ids[0], Item)
        find_ids = self.test_folder.filter(categories__contains=item.categories).values_list('item_id', 'changekey')
        self.assertEqual(len(find_ids), 1)
        self.assertEqual(len(find_ids[0]), 2, find_ids[0])
        self.assertEqual(insert_ids, list(find_ids))
        # Test with generator as argument
        item = list(self.account.fetch(ids=(i for i in find_ids)))[0]
        for f in self.ITEM_CLASS.FIELDS:
            if f.is_read_only:
                continue
            if f.name == 'resources':
                # The test server doesn't have any resources
                continue
            if f.name == 'attachments':
                # Attachments are handled separately
                continue
            if f.name == 'reminder_due_by':
                # EWS sets a default value if it is not set on insert. Ignore
                continue
            if f.is_list:
                self.assertSetEqual(set(getattr(item, f.name) or ()), set(insert_kwargs[f.name] or ()),
                                    (f.name, repr(item), insert_kwargs))
            else:
                self.assertEqual(getattr(item, f.name), insert_kwargs[f.name], (f.name, repr(item), insert_kwargs))

        # Test update
        update_kwargs = self.get_random_update_kwargs(item=item, insert_kwargs=insert_kwargs)
        update_fieldnames = update_kwargs.keys()
        for k, v in update_kwargs.items():
            setattr(item, k, v)
        # Test with generator as argument
        update_ids = self.account.bulk_update(items=(i for i in [(item, update_fieldnames)]))
        self.assertEqual(len(update_ids), 1)
        self.assertEqual(len(update_ids[0]), 2, update_ids)
        self.assertEqual(insert_ids[0].item_id, update_ids[0][0])  # ID should be the same
        self.assertNotEqual(insert_ids[0].changekey, update_ids[0][1])  # Changekey should not be the same when item is updated
        item = list(self.account.fetch(update_ids))[0]
        for f in self.ITEM_CLASS.FIELDS:
            if f.is_read_only:
                continue
            if f.name == 'resources':
                # The test server doesn't have any resources
                continue
            if f.name == 'attachments':
                # Attachments are handled separately
                continue
            if f.is_list:
                self.assertSetEqual(set(getattr(item, f.name) or ()), set(update_kwargs[f.name] or ()),
                                    (f.name, repr(item), update_kwargs))
            else:
                self.assertEqual(getattr(item, f.name), update_kwargs[f.name], (f.name, repr(item), update_kwargs))

        # Test wiping or removing fields
        wipe_kwargs = {}
        for f in self.ITEM_CLASS.FIELDS:
            if f.is_required or f.is_required_after_save:
                # These cannot be deleted
                continue
            if f.is_read_only:
                # These cannot be changed
                continue
            if f.name == 'attachments':
                continue
            if f.is_list:
                wipe_kwargs[f.name] = []
            wipe_kwargs[f.name] = None
        for k, v in wipe_kwargs.items():
            setattr(item, k, v)
        wipe_ids = self.account.bulk_update([(item, update_fieldnames), ])
        self.assertEqual(len(wipe_ids), 1)
        self.assertEqual(len(wipe_ids[0]), 2, wipe_ids)
        self.assertEqual(insert_ids[0].item_id, wipe_ids[0][0])  # ID should be the same
        self.assertNotEqual(insert_ids[0].changekey,
                            wipe_ids[0][1])  # Changekey should not be the same when item is updated
        item = list(self.account.fetch(wipe_ids))[0]
        for f in self.ITEM_CLASS.FIELDS:
            if f.is_required or f.is_required_after_save:
                continue
            if f.is_read_only:
                continue
            if f.name == 'attachments':
                continue
            if f.is_list:
                wipe_kwargs[f.name] = []
            wipe_kwargs[f.name] = None
            self.assertEqual(getattr(item, f.name), wipe_kwargs[f.name], (f.name, repr(item), insert_kwargs))

        # Test extern_id = None, which deletes the extended property entirely
        extern_id = None
        item.extern_id = extern_id
        wipe2_ids = self.account.bulk_update([(item, ['extern_id']), ])
        self.assertEqual(len(wipe2_ids), 1)
        self.assertEqual(len(wipe2_ids[0]), 2, wipe2_ids)
        self.assertEqual(insert_ids[0].item_id, wipe2_ids[0][0])  # ID should be the same
        self.assertNotEqual(insert_ids[0].changekey, wipe2_ids[0][1])  # Changekey should not be the same when item is updated
        item = list(self.account.fetch(wipe2_ids))[0]
        self.assertEqual(item.extern_id, extern_id)

        # Remove test item. Test with generator as argument
        self.bulk_delete(ids=(i for i in wipe2_ids))

    def test_export_and_upload(self):
        # 15 new items which we will attempt to export and re-upload
        items = [self.get_test_item().save() for _ in range(15)]
        ids = [(i.item_id, i.changekey) for i in items]
        # re-fetch items because there will be some extra fields added by the server
        items = list(self.test_folder.fetch(items))

        # Try exporting and making sure we get the right response
        export_results = self.account.export(items)
        self.assertEqual(len(items), len(export_results))
        for result in export_results:
            self.assertIsInstance(result, str)

        # Try reuploading our results
        upload_results = self.account.upload([(self.test_folder, data) for data in export_results])
        self.assertEqual(len(items), len(upload_results))
        for result in upload_results:
            # Must be a completely new ItemId
            self.assertIsInstance(result, tuple)
            self.assertNotIn(result, ids)

        # Check the items uploaded are the same as the original items
        def to_dict(item):
            dict_item = {}
            # fieldnames is everything except the ID so we'll use it to compare
            for f in item.FIELDS:
                # datetime_created and last_modified_time aren't copied, but instead are added to the new item after
                # uploading. This means mime_content can also change. Items also get new IDs on upload.
                if f.name in {'item_id', 'changekey', 'datetime_created', 'last_modified_time', 'mime_content'}:
                    continue
                dict_item[f.name] = getattr(item, f.name)
                if f.name == 'attachments':
                    # Attachments get new IDs on upload. Wipe them here so we can compare the other fields
                    for a in dict_item[f.name]:
                        a.attachment_id = None
            return dict_item

        uploaded_items = sorted([to_dict(item) for item in self.test_folder.fetch(upload_results)],
                                key=lambda i: i['subject'])
        original_items = sorted([to_dict(item) for item in items], key=lambda i: i['subject'])
        self.assertListEqual(original_items, uploaded_items)

        # Clean up after ourselves
        self.bulk_delete(ids=upload_results)
        self.bulk_delete(ids=ids)

    def test_export_with_error(self):
        # 15 new items which we will attempt to export and re-upload
        items = [self.get_test_item().save() for _ in range(15)]
        # Use id tuples for export here because deleting an item clears it's
        #  id.
        ids = [(item.item_id, item.changekey) for item in items]
        # Delete one of the items, this will cause an error
        items[3].delete(affected_task_occurrences=ALL_OCCURRENCIES)

        export_results = self.account.export(ids)
        self.assertEqual(len(items), len(export_results))
        for idx, result in enumerate(export_results):
            if idx == 3:
                # If it is the one returning the error
                self.assertIsInstance(result, ErrorItemNotFound)
            else:
                self.assertIsInstance(result, str)

        # Clean up after yourself
        del ids[3]  # Sending the deleted one through will cause an error
        self.bulk_delete(ids)

    def test_register(self):
        # Tests that we can register and de-register custom extended properties
        class TestProp(ExtendedProperty):
            property_set_id = 'deadbeaf-cafe-cafe-cafe-deadbeefcafe'
            property_name = 'Test Property'
            property_type = 'Integer'

        attr_name = 'dead_beef'

        # Before register
        self.assertNotIn(attr_name, self.ITEM_CLASS.fieldnames())
        with self.assertRaises(ValueError):
            self.ITEM_CLASS.deregister(attr_name)  # Not registered yet
        with self.assertRaises(ValueError):
            self.ITEM_CLASS.deregister('subject')  # Not an extended property

        self.ITEM_CLASS.register(attr_name=attr_name, attr_cls=TestProp)

        # After register
        self.assertEqual(TestProp.python_type(), int)
        self.assertIn(attr_name, self.ITEM_CLASS.fieldnames())

        # Test item creation, refresh, and update
        item = self.get_test_item(folder=self.test_folder)
        prop_val = item.dead_beef
        self.assertTrue(isinstance(prop_val, int))
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(prop_val, item.dead_beef)
        new_prop_val = get_random_int()
        item.dead_beef = new_prop_val
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(new_prop_val, item.dead_beef)

        # Test deregister
        with self.assertRaises(ValueError):
            self.ITEM_CLASS.register(attr_name=attr_name, attr_cls=TestProp)  # Already registered
        with self.assertRaises(ValueError):
            self.ITEM_CLASS.register(attr_name='XXX', attr_cls=Mailbox)  # Not an extended property
        self.ITEM_CLASS.deregister(attr_name=attr_name)
        self.assertNotIn(attr_name, self.ITEM_CLASS.fieldnames())

    def test_extended_property_arraytype(self):
        # Tests array type extended properties
        class TestArayProp(ExtendedProperty):
            property_set_id = 'deadcafe-beef-beef-beef-deadcafebeef'
            property_name = 'Test Array Property'
            property_type = 'IntegerArray'

        attr_name = 'dead_beef_array'
        self.ITEM_CLASS.register(attr_name=attr_name, attr_cls=TestArayProp)

        # Test item creation, refresh, and update
        item = self.get_test_item(folder=self.test_folder)
        prop_val = item.dead_beef_array
        self.assertTrue(isinstance(prop_val, list))
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(prop_val, item.dead_beef_array)
        new_prop_val = self.random_val(self.ITEM_CLASS.get_field_by_fieldname(attr_name))
        item.dead_beef_array = new_prop_val
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(new_prop_val, item.dead_beef_array)

        self.ITEM_CLASS.deregister(attr_name=attr_name)

    def test_extended_property_with_tag(self):
        class Flag(ExtendedProperty):
            property_tag = 0x1090
            property_type = 'Integer'

        attr_name = 'my_flag'
        self.ITEM_CLASS.register(attr_name=attr_name, attr_cls=Flag)

        # Test item creation, refresh, and update
        item = self.get_test_item(folder=self.test_folder)
        prop_val = item.my_flag
        self.assertTrue(isinstance(prop_val, int))
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(prop_val, item.my_flag)
        new_prop_val = self.random_val(self.ITEM_CLASS.get_field_by_fieldname(attr_name))
        item.my_flag = new_prop_val
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(new_prop_val, item.my_flag)

        self.ITEM_CLASS.deregister(attr_name=attr_name)

    def test_extended_distinguished_property(self):
        class MyMeeting(ExtendedProperty):
            distinguished_property_set_id = 'Meeting'
            property_type = 'Binary'
            property_id = 3

        attr_name = 'my_meeting'
        self.ITEM_CLASS.register(attr_name=attr_name, attr_cls=MyMeeting)

        # Test item creation, refresh, and update
        item = self.get_test_item(folder=self.test_folder)
        prop_val = item.my_meeting
        self.assertTrue(isinstance(prop_val, bytes))
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(prop_val, item.my_meeting)
        new_prop_val = self.random_val(self.ITEM_CLASS.get_field_by_fieldname(attr_name))
        item.my_meeting = new_prop_val
        item.save()
        item = list(self.account.fetch(ids=[(item.item_id, item.changekey)]))[0]
        self.assertEqual(new_prop_val, item.my_meeting)

        self.ITEM_CLASS.deregister(attr_name=attr_name)

    def test_attachment_failure(self):
        att1 = FileAttachment(name='my_file_1.txt', content=u'Hello from unicode æøå'.encode('utf-8'))
        att1.attachment_id = 'XXX'
        with self.assertRaises(ValueError):
            att1.attach()  # Cannot have an attachment ID
        att1.attachment_id = None
        with self.assertRaises(ValueError):
            att1.attach()  # Must have a parent item
        att1.parent_item = Item()
        with self.assertRaises(ValueError):
            att1.attach()  # Parent item must have an account
        att1.parent_item = None
        with self.assertRaises(ValueError):
            att1.detach()  # Must have an attachment ID
        att1.attachment_id = 'XXX'
        with self.assertRaises(ValueError):
            att1.attach()  # Must have a parent item
        att1.parent_item = Item()
        with self.assertRaises(ValueError):
            att1.attach()  # Parent item must have an account
        att1.parent_item = None
        att1.attachment_id = None

    def test_file_attachments(self):
        item = self.get_test_item(folder=self.test_folder)

        # Test __init__(attachments=...) and attach() on new item
        binary_file_content = u'Hello from unicode æøå'.encode('utf-8')
        att1 = FileAttachment(name='my_file_1.txt', content=binary_file_content)
        att1.content = binary_file_content  # Test property setter
        self.assertEqual(len(item.attachments), 0)
        item.attach(att1)
        self.assertEqual(len(item.attachments), 1)
        item.save()
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        self.assertEqual(len(fresh_item.attachments), 1)
        fresh_attachments = sorted(fresh_item.attachments, key=lambda a: a.name)
        self.assertEqual(fresh_attachments[0].name, 'my_file_1.txt')
        self.assertEqual(fresh_attachments[0].content, binary_file_content)

        # Test raw call to service
        self.assertEqual(
            list(GetAttachment(account=item.account).call(
                items=[att1.attachment_id],
                include_mime_content=False)
            )[0].find('{%s}Content' % TNS).text,
            'SGVsbG8gZnJvbSB1bmljb2RlIMOmw7jDpQ==')

        # Test attach on saved object
        att2 = FileAttachment(name='my_file_2.txt', content=binary_file_content)
        self.assertEqual(len(item.attachments), 1)
        item.attach(att2)
        self.assertEqual(len(item.attachments), 2)
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        self.assertEqual(len(fresh_item.attachments), 2)
        fresh_attachments = sorted(fresh_item.attachments, key=lambda a: a.name)
        self.assertEqual(fresh_attachments[0].name, 'my_file_1.txt')
        self.assertEqual(fresh_attachments[0].content, binary_file_content)
        self.assertEqual(fresh_attachments[1].name, 'my_file_2.txt')
        self.assertEqual(fresh_attachments[1].content, binary_file_content)

        # Test detach
        item.detach(att1)
        self.assertTrue(att1.attachment_id is None)
        self.assertTrue(att1.parent_item is None)
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        self.assertEqual(len(fresh_item.attachments), 1)
        fresh_attachments = sorted(fresh_item.attachments, key=lambda a: a.name)
        self.assertEqual(fresh_attachments[0].name, 'my_file_2.txt')
        self.assertEqual(fresh_attachments[0].content, binary_file_content)

    def test_item_attachments(self):
        item = self.get_test_item(folder=self.test_folder)
        item.attachments = []

        attached_item1 = self.get_test_item(folder=self.test_folder)
        attached_item1.attachments = []
        if hasattr(attached_item1, 'is_all_day'):
            attached_item1.is_all_day = False
        attached_item1.save()
        attachment1 = ItemAttachment(name='attachment1', item=attached_item1)
        attachment1.item = attached_item1  # Test property setter
        item.attach(attachment1)

        self.assertEqual(len(item.attachments), 1)
        item.save()
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        self.assertEqual(len(fresh_item.attachments), 1)
        fresh_attachments = sorted(fresh_item.attachments, key=lambda a: a.name)
        self.assertEqual(fresh_attachments[0].name, 'attachment1')
        self.assertIsInstance(fresh_attachments[0].item, self.ITEM_CLASS)

        for f in self.ITEM_CLASS.FIELDS:
            # Normalize some values we don't control
            if f.is_read_only:
                continue
            if isinstance(f, ExtendedPropertyField):
                # Attachments don't have these values. It may be possible to request it if we can find the FieldURI
                continue
            if f.name == 'is_read':
                # This is always true for item attachments?
                continue
            if f.name == 'reminder_due_by':
                # EWS sets a default value if it is not set on insert. Ignore
                continue
            old_val = getattr(attached_item1, f.name)
            new_val = getattr(fresh_attachments[0].item, f.name)
            if f.is_list:
                old_val, new_val = set(old_val or ()), set(new_val or ())
            self.assertEqual(old_val, new_val, (f.name, old_val, new_val))

        # Test attach on saved object
        attached_item2 = self.get_test_item(folder=self.test_folder)
        attached_item2.attachments = []
        if hasattr(attached_item2, 'is_all_day'):
            attached_item2.is_all_day = False
        attached_item2.save()
        attachment2 = ItemAttachment(name='attachment2', item=attached_item2)
        item.attach(attachment2)

        self.assertEqual(len(item.attachments), 2)
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        self.assertEqual(len(fresh_item.attachments), 2)
        fresh_attachments = sorted(fresh_item.attachments, key=lambda a: a.name)
        self.assertEqual(fresh_attachments[0].name, 'attachment1')
        self.assertIsInstance(fresh_attachments[0].item, self.ITEM_CLASS)

        for f in self.ITEM_CLASS.FIELDS:
            # Normalize some values we don't control
            if f.is_read_only:
                continue
            if isinstance(f, ExtendedPropertyField):
                # Attachments don't have these values. It may be possible to request it if we can find the FieldURI
                continue
            if f.name == 'reminder_due_by':
                # EWS sets a default value if it is not set on insert. Ignore
                continue
            if f.name == 'is_read':
                # This is always true for item attachments?
                continue
            old_val = getattr(attached_item1, f.name)
            new_val = getattr(fresh_attachments[0].item, f.name)
            if f.is_list:
                old_val, new_val = set(old_val or ()), set(new_val or ())
            self.assertEqual(old_val, new_val, (f.name, old_val, new_val))

        self.assertEqual(fresh_attachments[1].name, 'attachment2')
        self.assertIsInstance(fresh_attachments[1].item, self.ITEM_CLASS)

        for f in self.ITEM_CLASS.FIELDS:
            # Normalize some values we don't control
            if f.is_read_only:
                continue
            if isinstance(f, ExtendedPropertyField):
                # Attachments don't have these values. It may be possible to request it if we can find the FieldURI
                continue
            if f.name == 'reminder_due_by':
                # EWS sets a default value if it is not set on insert. Ignore
                continue
            if f.name == 'is_read':
                # This is always true for item attachments?
                continue
            old_val = getattr(attached_item2, f.name)
            new_val = getattr(fresh_attachments[1].item, f.name)
            if f.is_list:
                old_val, new_val = set(old_val or ()), set(new_val or ())
            self.assertEqual(old_val, new_val, (f.name, old_val, new_val))

        # Test detach
        item.detach(attachment2)
        self.assertTrue(attachment2.attachment_id is None)
        self.assertTrue(attachment2.parent_item is None)
        fresh_item = list(self.account.fetch(ids=[item]))[0]
        self.assertEqual(len(fresh_item.attachments), 1)
        fresh_attachments = sorted(fresh_item.attachments, key=lambda a: a.name)

        for f in self.ITEM_CLASS.FIELDS:
            # Normalize some values we don't control
            if f.is_read_only:
                continue
            if isinstance(f, ExtendedPropertyField):
                # Attachments don't have these values. It may be possible to request it if we can find the FieldURI
                continue
            if f.name == 'reminder_due_by':
                # EWS sets a default value if it is not set on insert. Ignore
                continue
            if f.name == 'is_read':
                # This is always true for item attachments?
                continue
            old_val = getattr(attached_item1, f.name)
            new_val = getattr(fresh_attachments[0].item, f.name)
            if f.is_list:
                old_val, new_val = set(old_val or ()), set(new_val or ())
            self.assertEqual(old_val, new_val, (f.name, old_val, new_val))

        # Test attach with non-saved item
        attached_item3 = self.get_test_item(folder=self.test_folder)
        attached_item3.attachments = []
        if hasattr(attached_item3, 'is_all_day'):
            attached_item3.is_all_day = False
        attachment3 = ItemAttachment(name='attachment2', item=attached_item3)
        item.attach(attachment3)
        item.detach(attachment3)

    def test_bulk_failure(self):
        # Test that bulk_* can handle EWS errors and return the errors in order without losing non-failure results
        items1 = [self.get_test_item().save() for _ in range(3)]
        items1[1].changekey = 'XXX'
        for i, res in enumerate(self.account.bulk_delete(items1, affected_task_occurrences=ALL_OCCURRENCIES)):
            if i == 1:
                self.assertIsInstance(res, ErrorInvalidChangeKey)
            else:
                self.assertEqual(res, True)
        items2 = [self.get_test_item().save() for _ in range(3)]
        items2[1].item_id = 'AAAA=='
        for i, res in enumerate(self.account.bulk_delete(items2, affected_task_occurrences=ALL_OCCURRENCIES)):
            if i == 1:
                self.assertIsInstance(res, ErrorInvalidIdMalformed)
            else:
                self.assertEqual(res, True)
        items3 = [self.get_test_item().save() for _ in range(3)]
        items3[1].item_id = items1[0].item_id
        for i, res in enumerate(self.account.fetch(items3)):
            if i == 1:
                self.assertIsInstance(res, ErrorItemNotFound)
            else:
                self.assertIsInstance(res, Item)


class CalendarTest(BaseItemTest):
    TEST_FOLDER = 'calendar'
    ITEM_CLASS = CalendarItem

    def test_view(self):
        item1 = self.ITEM_CLASS(
            account=self.account,
            folder=self.test_folder,
            subject=get_random_string(16),
            start=self.tz.localize(EWSDateTime(2016, 1, 1, 8)),
            end=self.tz.localize(EWSDateTime(2016, 1, 1, 10)),
            categories=self.categories,
        )
        item2 = self.ITEM_CLASS(
            account=self.account,
            folder=self.test_folder,
            subject=get_random_string(16),
            start=self.tz.localize(EWSDateTime(2016, 2, 1, 8)),
            end=self.tz.localize(EWSDateTime(2016, 2, 1, 10)),
            categories=self.categories,
        )
        self.test_folder.bulk_create(items=[item1, item2])

        # Test missing args
        with self.assertRaises(TypeError):
            self.test_folder.view()
        # Test bad args
        with self.assertRaises(ValueError):
            list(self.test_folder.view(start=item1.end, end=item1.start))
        with self.assertRaises(ValueError):
            list(self.test_folder.view(start='xxx', end=item1.end))
        with self.assertRaises(ValueError):
            list(self.test_folder.view(start=item1.start, end=item1.end, max_items=0))

        def match_cat(i):
            return set(i.categories) == set(self.categories)

        # Test dates
        self.assertEqual(len([i for i in self.test_folder.view(start=item1.start, end=item1.end) if match_cat(i)]), 1)
        self.assertEqual(len([i for i in self.test_folder.view(start=item1.start, end=item2.end) if match_cat(i)]), 2)
        # Edge cases. Get view from end of item1 to start of item2. Should logically return 0 items, but Exchange wants
        # it differently and returns item1 even though there is no overlap.
        self.assertEqual(len([i for i in self.test_folder.view(start=item1.end, end=item2.start) if match_cat(i)]), 1)
        self.assertEqual(len([i for i in self.test_folder.view(start=item1.start, end=item2.start) if match_cat(i)]), 1)

        # Test max_items
        self.assertEqual(len([i for i in self.test_folder.view(start=item1.start, end=item2.end, max_items=9999) if match_cat(i)]), 2)
        self.assertEqual(len(self.test_folder.view(start=item1.start, end=item2.end, max_items=1)), 1)

        # Test chaining
        qs = self.test_folder.view(start=item1.start, end=item2.end)
        self.assertTrue(qs.count() >= 2)
        with self.assertRaises(ErrorInvalidOperation):
            qs.filter(subject=item1.subject).count()  # EWS does not allow restrictions
        self.assertListEqual(
            [i for i in qs.order_by('subject').values('subject') if i['subject'] in (item1.subject, item2.subject)],
            [{'subject': s} for s in sorted([item1.subject, item2.subject])]
        )


class MessagesTest(BaseItemTest):
    # Just test one of the Message-type folders
    TEST_FOLDER = 'inbox'
    ITEM_CLASS = Message

    def test_send(self):
        # Test that we can send (only) Message items
        item = self.get_test_item()
        item.folder = None
        item.send()
        self.assertIsNone(item.item_id)
        self.assertIsNone(item.changekey)
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 0)

    def test_send_and_save(self):
        # Test that we can send_and_save Message items
        item = self.get_test_item()
        item.send_and_save()
        self.assertIsNone(item.item_id)
        self.assertIsNone(item.changekey)
        time.sleep(5)  # Requests are supposed to be transactional, but apparently not...
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 1)

        # Test update, although it makes little sense
        item = self.get_test_item()
        item.save()
        item.send_and_save()
        time.sleep(5)  # Requests are supposed to be transactional, but apparently not...
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 1)

    def test_send_draft(self):
        item = self.get_test_item()
        item.folder = self.account.drafts
        item.is_draft = True
        item.save()  # Save a draft
        item.send()  # Send the draft
        self.assertIsNone(item.item_id)
        self.assertIsNone(item.changekey)
        self.assertIsNone(item.folder)
        self.assertEqual(len(self.test_folder.filter(categories__contains=item.categories)), 0)

    def test_send_and_copy_to_folder(self):
        item = self.get_test_item()
        item.send(save_copy=True, copy_to_folder=self.account.sent)  # Send the draft and save to the sent folder
        self.assertIsNone(item.item_id)
        self.assertIsNone(item.changekey)
        self.assertEqual(item.folder, self.account.sent)
        time.sleep(5)  # Requests are supposed to be transactional, but apparently not...
        self.assertEqual(len(self.account.sent.filter(categories__contains=item.categories)), 1)

    def test_bulk_send(self):
        with self.assertRaises(AttributeError):
            self.account.bulk_send(ids=[], save_copy=False, copy_to_folder=self.account.trash)
        item = self.get_test_item()
        item.save()
        for res in self.account.bulk_send(ids=[item]):
            self.assertEqual(res, True)
        time.sleep(10)  # Requests are supposed to be transactional, but apparently not...
        # By default, sent items are placed in the sent folder
        ids = self.account.sent.filter(categories__contains=item.categories).values_list('item_id', 'changekey')
        self.assertEqual(len(ids), 1)
        self.bulk_delete(ids)


class TasksTest(BaseItemTest):
    TEST_FOLDER = 'tasks'
    ITEM_CLASS = Task


class ContactsTest(BaseItemTest):
    TEST_FOLDER = 'contacts'
    ITEM_CLASS = Contact

    def test_paging(self):
        # TODO: This test throws random ErrorIrresolvableConflict errors on item creation for some reason.
        pass


def get_random_bool():
    return bool(random.randint(0, 1))


def get_random_int(min=0, max=2147483647):
    return random.randint(min, max)


def get_random_decimal(min=0, max=100):
    precision = 2
    val = get_random_int(min, max * 10**precision) / 10.0**precision
    return Decimal('{:.2f}'.format(val))


def get_random_choice(choices):
    return random.sample(choices, 1)[0]


def get_random_string(length, spaces=True, special=True):
    chars = string.ascii_letters + string.digits
    if special:
        chars += ':.-_'
    if spaces:
        chars += ' '
    # We want random strings that don't end in spaces - Exchange strips these
    res = ''.join(map(lambda i: random.choice(chars), range(length))).strip()
    if len(res) < length:
        # If strip() made the string shorter, make sure to fill it up
        res += get_random_string(length - len(res), spaces=False)
    return res


def get_random_url():
    path_len = random.randint(1, 16)
    domain_len = random.randint(1, 30)
    tld_len = random.randint(2, 4)
    return 'http://%s.%s/%s.html' % tuple(map(
        lambda i: get_random_string(i, spaces=False, special=False).lower(),
        (domain_len, tld_len, path_len)
    ))


def get_random_email():
    account_len = random.randint(1, 6)
    domain_len = random.randint(1, 30)
    tld_len = random.randint(2, 4)
    return '%s@%s.%s' % tuple(map(
        lambda i: get_random_string(i, spaces=False, special=False).lower(),
        (account_len, domain_len, tld_len)
    ))


def get_random_date(start_date=datetime.date(1900, 1, 1), end_date=datetime.date(2100, 1, 1)):
    return EWSDate.fromordinal(random.randint(start_date.toordinal(), end_date.toordinal()))


def get_random_datetime(start_date=datetime.date(1900, 1, 1), end_date=datetime.date(2100, 1, 1)):
    # Create a random datetime with minute precision
    random_date = get_random_date(start_date=start_date, end_date=end_date)
    random_datetime = datetime.datetime.combine(random_date, datetime.time.min) \
                      + datetime.timedelta(minutes=random.randint(0, 60 * 24))
    return UTC.localize(EWSDateTime.from_datetime(random_datetime))


def get_random_datetime_range(start_date=datetime.date(1900, 1, 1), end_date=datetime.date(2100, 1, 1)):
    # Create two random datetimes. Calendar items raise ErrorCalendarDurationIsTooLong if duration is > 5 years.
    dt1 = get_random_datetime(start_date=start_date, end_date=end_date)
    dt2 = dt1 + datetime.timedelta(minutes=random.randint(0, 60 * 24 * 365 * 5))
    return dt1, dt2


if __name__ == '__main__':
    import logging

    loglevel = logging.DEBUG
    # loglevel = logging.WARNING
    logging.basicConfig(level=loglevel)
    logging.getLogger('exchangelib').setLevel(loglevel)
    unittest.main()
