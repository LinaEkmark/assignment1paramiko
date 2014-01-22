# Copyright (C) 2013-2014 science + computing ag
# Author: Sebastian Deiss <sebastian.deiss@t-online.de>
#
#
# This file is part of paramiko.
#
# Paramiko is free software; you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# Paramiko is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Paramiko; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA.

"""
This module provides GSS-API / SSPI authentication for Paramiko as defined in
RFC 4462 with the following restrictions:
Credential delegation is not supported in server mode,
GSS-API key exchange is supported, but not implemented in Paramiko.
To Use this module, you need the following additional python packages:
U{pyasn1 >= 0.1.7 <https://pypi.python.org/pypi/pyasn1>},
U{python-gssapi >= 0.4.0 (Unix) <https://pypi.python.org/pypi/python-gssapi>},
U{pywin32 2.1.8 (Windows) <sourceforge.net/projects/pywin32/>}.

@summary: SSH2 GSS-API / SSPI authentication module
@version: 0.1
@author: Sebastian Deiss
@contact: U{https://github.com/SebastianDeiss/paramiko/issues}
@organization: science + computing ag
               (U{EMail<mailto:a.kruis@science-computing.de>})
@copyright: (C) 2013-2014 U{science + computing ag
            <https://www.science-computing.de>}
@license: GNU Lesser General Public License (LGPL)
@see: L{kex_gss}

Created on 07.11.2013
"""

import struct
import os
try:
    from pyasn1.type.univ import ObjectIdentifier
    from pyasn1.codec.der import encoder, decoder
except ImportError:
    class ObjectIdentifier(object):
        def __init__(self, *args):
            raise NotImplementedError("Module pyasn1 not importable")

    class decoder(object):
        def decode(self):
            raise NotImplementedError("Module pyasn1 not importable")

from paramiko.common import MSG_USERAUTH_REQUEST
from paramiko.ssh_exception import SSHException

"""
@var _API: constraint for the used API
@type _API: String
"""
_API = "MIT"

try:
    import gssapi
except ImportError:
    try:
        import sspicon
        import sspi
        _API = "SSPI"
    except ImportError:
        _API = None


def GSSAuth(auth_method, gss_deleg_creds=True):
    """
    Provide SSH2 GSS-API / SSPI authentication for Paramiko.

    @param auth_method: The name of the SSH authentication mechanism
                        (gssapi-with-mic or gss-keyex)
    @type auth_method: String
    @param gss_deleg_creds: Delegate client credentials or not.
                            We delegate credentials by default.
    @type gss_deleg_creds: Boolean
    @return: Either an L{_SSH_GSSAPI} (Unix) object or an
             L{_SSH_SSPI} (Windows) object
    @rtype: Object
    @see: U{RFC 4462 <www.ietf.org/rfc/rfc4462.txt>}
    @note: Check for the available API and return either an L{_SSH_GSSAPI}
           (MIT GSSAPI) object or an L{_SSH_SSPI} (MS SSPI) object. If you
           get python-gssapi working on Windows, python-gssapi
           will be used and a L{_SSH_GSSAPI} object will be returned.
           If there is no supported API available,
           C{None} will be returned.
    """
    if _API == "MIT":
        return _SSH_GSSAPI(auth_method, gss_deleg_creds)
    elif _API == "SSPI" and os.name == "nt":
        return _SSH_SSPI(auth_method, gss_deleg_creds)
    else:
        return None


class _SSH_GSSAuth(object):
    """
    Contains the shared variables and methods of L{_SSH_GSSAPI} and
    L{_SSH_SSPI}.
    """
    def __init__(self, auth_method, gss_deleg_creds):
        """
        @param auth_method: The name of the SSH authentication mechanism
                            (gssapi-with-mic or gss-keyex)
        @type auth_method: String
        @param gss_deleg_creds: Delegate client credentials or not
        @type gss_deleg_creds: Boolean
        """
        self._auth_method = auth_method
        self._gss_deleg_creds = gss_deleg_creds
        self._gss_host = None
        self._username = None
        self._session_id = None
        self._service = "ssh-connection"
        """
        OpenSSH supports Kerberos V5 mechanism only for GSS-API authentication,
        so we also support the krb5 mechanism only.
        """
        self._krb5_mech = "1.2.840.113554.1.2.2"

        # client mode
        self._gss_ctxt = None
        self._gss_ctxt_status = False

        # server mode
        self._gss_srv_ctxt = None
        self._gss_srv_ctxt_status = False
        self.cc_file = None

    def set_service(self, service):
        """
        This is just a setter to use a non default service.
        I added this method, because RFC 4462 doesn't specify "ssh-connection"
        as the only service value.

        @param service: The desired SSH service
        @type service: String
        @rtype: Void
        """
        if service.find("ssh-"):
            self._service = service

    def set_username(self, username):
        """
        Setter for C{username}. If GSS-API Key Exchange is performed, the
        username is not set by C{ssh_init_sec_context}.

        @param username: The name of the user who attempts to login
        @type username: String
        @rtype: Void
        """
        self._username = username

    def ssh_gss_oids(self, mode="client"):
        """
        This method returns a single OID, because we only support the
        Kerberos V5 mechanism.

        @param mode: Client for client mode and server for server mode
        @param mode: String
        @return: A byte sequence containing the number of supported
                 OIDs, the length of the OID and the actual OID encoded with
                 DER
        @note: In server mode we just return the OID length and the DER encoded
               OID.
        @rtype: Bytes
        """
        OIDs = self._make_uint32(1)
        krb5_OID = encoder.encode(ObjectIdentifier(self._krb5_mech))
        OID_len = self._make_uint32(len(krb5_OID))
        if mode == "server":
            return OID_len + krb5_OID
        return OIDs + OID_len + krb5_OID

    def ssh_check_mech(self, desired_mech):
        """
        Check if the given OID is the Kerberos V5 OID (server mode).

        @param desired_mech: The desired GSS-API mechanism of the client
        @type desired_mech: String
        @return: C{True} if the given OID is supported, otherwise C{False}
        @rtype: Boolean
        """
        mech, __ = decoder.decode(desired_mech)
        if mech.__str__() != self._krb5_mech:
            return False
        return True

    # Internals
    #--------------------------------------------------------------------------
    def _make_uint32(self, integer):
        """
        Create a 32 bit unsigned integer (The byte sequence of an integer).

        @param integer: The integer value to convert
        @type integer: Integer
        @return: The byte sequence of an 32 bit integer
        @rtype: Bytes
        """
        return struct.pack("!I", integer)

    def _ssh_build_mic(self, session_id, username, service, auth_method):
        """
        Create the SSH2 MIC filed for gssapi-with-mic.

        @param session_id: The SSH session ID
        @type session_id: String
        @param username: The name of the user who attempts to login
        @type username: String
        @param service: The requested SSH service
        @type service: String
        @param auth_method: The requested SSH authentication mechanism
        @type auth_method: String
        @return: The MIC as defined in RFC 4462. The contents of the
                 MIC field are:
                 string    session_identifier,
                 byte      SSH_MSG_USERAUTH_REQUEST,
                 string    user-name,
                 string    service (ssh-connection),
                 string    authentication-method (gssapi-with-mic or gss-keyex)
        @rtype: Bytes
        """
        mic = self._make_uint32(len(session_id))
        mic += session_id
        mic += chr(MSG_USERAUTH_REQUEST)
        mic += self._make_uint32(len(username))
        mic += str.encode(username)
        mic += self._make_uint32(len(service))
        mic += str.encode(service)
        mic += self._make_uint32(len(auth_method))
        mic += str.encode(auth_method)
        return mic


class _SSH_GSSAPI(_SSH_GSSAuth):
    """
    Implementation of the GSS-API MIT Kerberos Authentication for SSH2.

    @see: L{GSSAuth}
    """
    def __init__(self, auth_method, gss_deleg_creds):
        """
        @param auth_method: The name of the SSH authentication mechanism
                            (gssapi-with-mic or gss-keyex)
        @type auth_method: String
        @param gss_deleg_creds: Delegate client credentials or not
        @type gss_deleg_creds: Boolean
        """
        _SSH_GSSAuth.__init__(self, auth_method, gss_deleg_creds)

        if self._gss_deleg_creds:
            self._gss_flags = (gssapi.C_PROT_READY_FLAG,
                               gssapi.C_INTEG_FLAG,
                               gssapi.C_MUTUAL_FLAG,
                               gssapi.C_DELEG_FLAG)
        else:
            self._gss_flags = (gssapi.C_PROT_READY_FLAG,
                               gssapi.C_INTEG_FLAG,
                               gssapi.C_MUTUAL_FLAG)

    def ssh_init_sec_context(self, target, desired_mech=None,
                             username=None, recv_token=None):
        """
        Initialize a GSS-API context.

        @param username: The name of the user who attempts to login
        @type username: String
        @param target: The hostname of the target to connect to
        @type target: String
        @param desired_mech: The negotiated GSS-API mechanism
                             ("pseudo negotiated" mechanism, because we
                             support just the krb5 mechanism :-))
        @type desired_mech: String
        @param recv_token: The GSS-API token received from the Server
        @type recv_token: String
        @raise SSHException: Is raised if the desired mechanism of the client
                             is not supported
        @return: A C{String} if the GSS-API has returned a token or C{None} if
                 no token was returned
        @rtype: String or None
        """
        self._username = username
        self._gss_host = target
        targ_name = gssapi.Name("host@" + self._gss_host,
                                gssapi.C_NT_HOSTBASED_SERVICE)
        ctx = gssapi.Context()
        ctx.flags = self._gss_flags
        if desired_mech is None:
            krb5_mech = gssapi.OID.mech_from_string(self._krb5_mech)
        else:
            mech, __ = decoder.decode(desired_mech)
            if mech.__str__() != self._krb5_mech:
                raise SSHException("Unsupported mechanism OID.")
            else:
                krb5_mech = gssapi.OID.mech_from_string(self._krb5_mech)
        token = None
        if recv_token is None:
            self._gss_ctxt = gssapi.InitContext(peer_name=targ_name,
                                                mech_type=krb5_mech,
                                                req_flags=ctx.flags)
            token = self._gss_ctxt.step(token)
        else:
            token = self._gss_ctxt.step(recv_token)
        self._gss_ctxt_status = self._gss_ctxt.established
        return token

    def ssh_get_mic(self, session_id, gss_kex=False):
        """
        Create the MIC token for a SSH2 message.

        @param session_id: The SSH session ID
        @type session_id: String
        @param gss_kex: Generate the MIC for GSS-API Key Exchange or not
        @type gss_kex: Boolean
        @return: gssapi-with-mic:
                 Returns the MIC token from GSS-API for the message we created
                 with C{_ssh_build_mic}.
                 gssapi-keyex:
                 Returns the MIC token from GSS-API with the SSH session ID as
                 message.
        @rtype: String
        @see: L{_ssh_build_mic}
        """
        self._session_id = session_id
        if not gss_kex:
            mic_field = self._ssh_build_mic(self._session_id,
                                            self._username,
                                            self._service,
                                            self._auth_method)
            mic_token = self._gss_ctxt.get_mic(mic_field)
        else:
            # for key exchange with gssapi-keyex
            mic_token = self._gss_srv_ctxt.get_mic(self._session_id)
        return mic_token

    def ssh_accept_sec_context(self, hostname, recv_token, username=None):
        """
        Accept a GSS-API context (server mode).

        @param hostname: The servers hostname
        @type hostname: String
        @param username: The name of the user who attempts to login
        @type username: String
        @param recv_token: The GSS-API Token received from the server, if it's
                           not the initial call
        @type recv_token: String
        @return: A C{String} if the GSS-API has returned a token or C{None} if
                 no token was returned
        @rtype: String or None
        """
        # hostname and username are not required for GSSAPI, but for SSPI
        self._gss_host = hostname
        self._username = username
        if self._gss_srv_ctxt is None:
            self._gss_srv_ctxt = gssapi.AcceptContext()
        token = self._gss_srv_ctxt.step(recv_token)
        self._gss_srv_ctxt_status = self._gss_srv_ctxt.established
        return token

    def ssh_check_mic(self, mic_token, session_id, username=None):
        """
        Verify the MIC token for a SSH2 message.

        @param mic_token: The MIC token received from the client
        @type mic_token: String
        @param session_id: The SSH session ID
        @type session_id: String
        @param username: The name of the user who attempts to login
        @type username: String
        @return: 0 if the MIC check was successful and 1 if it fails
        @rtype: Integer
        """
        self._session_id = session_id
        self._username = username
        if self._username is not None:
            # server mode
            mic_field = self._ssh_build_mic(self._session_id,
                                        self._username,
                                        self._service,
                                        self._auth_method)
            mic_status = self._gss_srv_ctxt.verify_mic(mic_field,
                                                       mic_token)
        else:
            # for key exchange with gssapi-keyex
            # client mode
            mic_status = self._gss_ctxt.verify_mic(self._session_id,
                                                   mic_token)
        return mic_status

    @property
    def credentials_delegated(self):
        """
        Checks if credentials are delegated (server mode).

        @return: C{True} if credentials are delegated, otherwise C{False}
        @rtype: Boolean
        """
        if self._gss_srv_ctxt.delegated_cred is not None:
            return True
        return False

    def save_client_creds(self, client_token):
        """
        Save the Client token in a file. This is used by the SSH server
        to store the client credentials if credentials are delegated
        (server mode).

        @param client_token: The GSS-API token received form the client
        @type client_token: String
        @raise NotImplementedError: Credential delegation is currently not
                                    supported in server mode
        """
        raise NotImplementedError


class _SSH_SSPI(_SSH_GSSAuth):
    """
    Implementation of the Microsoft SSPI Kerberos Authentication for SSH2.

    @see: L{GSSAuth}
    """
    def __init__(self, auth_method, gss_deleg_creds):
        """
        @param auth_method: The name of the SSH authentication mechanism
                            (gssapi-with-mic or gss-keyex)
        @type auth_method: String
        @param gss_deleg_creds: Delegate client credentials or not
        @type gss_deleg_creds: Boolean
        """
        _SSH_GSSAuth.__init__(self, auth_method, gss_deleg_creds)

        if self._gss_deleg_creds:
            self._gss_flags = sspicon.ISC_REQ_INTEGRITY |\
                              sspicon.ISC_REQ_MUTUAL_AUTH |\
                              sspicon.ISC_REQ_DELEGATE
        else:
            self._gss_flags = sspicon.ISC_REQ_INTEGRITY |\
                              sspicon.ISC_REQ_MUTUAL_AUTH

    def ssh_init_sec_context(self, target, desired_mech=None,
                             username=None, recv_token=None):
        """
        Initialize a SSPI context.

        @param username: The name of the user who attempts to login
        @type username: String
        @param target: The FQDN of the target to connect to
        @type target: String
        @param desired_mech: The negotiated SSPI mechanism
                             ("pseudo negotiated" mechanism, because we
                             support just the krb5 mechanism :-))
        @type desired_mech: String
        @param recv_token: The SSPI token received from the Server
        @type recv_token: String
        @raise SSHException: Is raised if the desired mechanism of the client
                             is not supported
        @return: A C{String} if the SSPI has returned a token or C{None} if
                 no token was returned
        @rtype: String or None
        """
        self._username = username
        self._gss_host = target
        targ_name = "host/" + self._gss_host
        if desired_mech is not None:
            mech, __ = decoder.decode(desired_mech)
            if mech.__str__() != self._krb5_mech:
                raise SSHException("Unsupported mechanism OID.")
        if recv_token is None:
            self._gss_ctxt = sspi.ClientAuth("Kerberos",
                                             scflags=self._gss_flags,
                                             targetspn=targ_name)
        error, token = self._gss_ctxt.authorize(recv_token)
        token = token[0].Buffer
        if error == 0:
            """
            if the status is GSS_COMPLETE (error = 0) the context is fully
            established an we can set _gss_ctxt_status to True.
            """
            self._gss_ctxt_status = True
            token = None
            """
            You won't get another token if the context is fully established,
            so i set token to None instead of ""
            """
        return token

    def ssh_get_mic(self, session_id, gss_kex=False):
        """
        Create the MIC token for a SSH2 message.

        @param session_id: The SSH session ID
        @type session_id: String
        @param gss_kex: Generate the MIC for Key Exchange with SSPI or not
        @type gss_kex: Boolean
        @return: gssapi-with-mic:
                 Returns the MIC token from SSPI for the message we created
                 with C{_ssh_build_mic}.
                 gssapi-keyex:
                 Returns the MIC token from SSPI with the SSH session ID as
                 message.
        @rtype: String
        @see: L{_ssh_build_mic}
        """
        self._session_id = session_id
        if not gss_kex:
            mic_field = self._ssh_build_mic(self._session_id,
                                            self._username,
                                            self._service,
                                            self._auth_method)
            mic_token = self._gss_ctxt.sign(mic_field)
        else:
            # for key exchange with gssapi-keyex
            mic_token = self._gss_srv_ctxt.sign(self._session_id)
        return mic_token

    def ssh_accept_sec_context(self, hostname, username, recv_token):
        """
        Accept a SSPI context (server mode).

        @param hostname: The servers FQDN
        @type hostname: String
        @param username: The name of the user who attempts to login
        @type username: String
        @param recv_token: The SSPI Token received from the server, if it's not
                           the initial call
        @type recv_token: String
        @return: A C{String} if the SSPI has returned a token or C{None} if
                 no token was returned
        @rtype: String or None
        """
        self._gss_host = hostname
        self._username = username
        targ_name = "host/" + self._gss_host
        self._gss_srv_ctxt = sspi.ServerAuth("Kerberos", spn=targ_name)
        error, token = self._gss_srv_ctxt.authorize(recv_token)
        token = token[0].Buffer
        if error == 0:
            self._gss_srv_ctxt_status = True
            token = None
        return token

    def ssh_check_mic(self, mic_token, session_id, username=None):
        """
        Verify the MIC token for a SSH2 message.

        @param mic_token: The MIC token received from the client
        @type mic_token: String
        @param session_id: The SSH session ID
        @type session_id: String
        @param username: The name of the user who attempts to login
        @type username: String
        @return: 0 if the MIC check was successful
        @rtype: Integer
        """
        self._session_id = session_id
        self._username = username
        mic_status = 1
        if username is not None:
            # server mode
            mic_field = self._ssh_build_mic(self._session_id,
                                            self._username,
                                            self._service,
                                            self._auth_method)
            mic_status = self._gss_srv_ctxt.verify(mic_field,
                                                   mic_token)
        else:
            # for key exchange with gssapi-keyex
            # client mode
            mic_status = self._gss_ctxt.verify(self._session_id,
                                               mic_token)
        """
        The SSPI method C{verify} has no return value, so if no SSPI error
        is returned, set C{mic_status} to 0.
        """
        mic_status = 0
        return mic_status

    @property
    def credentials_delegated(self):
        """
        Checks if credentials are delegated (server mode).

        @return: C{True} if credentials are delegated, otherwise C{False}
        @rtype: Boolean
        """
        return (
                self._gss_flags & sspicon.ISC_REQ_DELEGATE
                ) and (
                self._gss_srv_ctxt_status or (self._gss_flags)
           )

    def save_client_creds(self, client_token):
        """
        Save the Client token in a file. This is used by the SSH server
        to store the client credentails if credentials are delegated
        (server mode).

        @param client_token: The SSPI token received form the client
        @type client_token: String
        @raise NotImplementedError: Credential delegation is currently not
                                    supported in server mode
        """
        raise NotImplementedError
