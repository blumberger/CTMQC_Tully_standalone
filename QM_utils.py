#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon May 13 12:25:43 2019

@author: oem
"""
import numpy as np
import scipy.integrate as integrate
import random as rd
import copy

import hamiltonian as Ham


def calc_ad_frc(pos, ctmqc_env):
    """
    Will calculate the forces from each adiabatic state (the grad E term)
    """
    dx = ctmqc_env['dx']
    H_xm = ctmqc_env['Hfunc'](pos - dx)
    H_x = ctmqc_env['Hfunc'](pos)
    H_xp = ctmqc_env['Hfunc'](pos + dx)
    allH = [H_xm, H_x, H_xp]
    allE = [Ham.getEigProps(H, ctmqc_env)[0] for H in allH]
    gradE = np.array(np.gradient(allE, dx, axis=0))[2]
    return -gradE


def calc_ad_mom(ctmqc_env, irep, v, ad_frc=False):
    """
    Will calculate the adiabatic momenta (time-integrated adiab force)
    """
    if ad_frc is False:
        pos = ctmqc_env['pos'][irep, v]
        ad_frc = calc_ad_frc(pos, ctmqc_env)
        
    ad_mom = ctmqc_env['adMom'][irep, v]
    dt = ctmqc_env['dt']

    ad_mom += dt * ad_frc
    return ad_mom


def gaussian(RIv, RJv, sigma):
    """
    Will calculate the gaussian on the traj

    This has been tested and is definitely normalised
    """
    sig2 = sigma**2

    pre_fact = (sig2 * 2 * np.pi)**(-0.5)
    exponent = -((RIv - RJv)**2 / (2 * sig2))

    return pre_fact * np.exp(exponent)


def test_norm_gauss():
    """
    Will test whether the gaussian is normalised by checking 50 different
    random gaussians.
    """
    x = np.arange(-13, 13, 0.001)
    y = [gaussian(x, -1, abs(rd.gauss(0.5, 0.3))+0.05) for i in range(50)]
    norms = [integrate.simps(i, x) for i in y]
    if any(abs(norm - 1) > 1e-9 for norm in norms):
        print(norms)
        raise SystemExit("Gaussian not normalised!")


def calc_nucl_dens_PP(R, sigma):
    """
    Will calculate the nuclear density post-production

    Inputs:
        * R => the positions for 1 step <np.array (nrep, natom)>
        * sigma => the nuclear widths for 1 step <np.array (nrep, natom)>
    """
    nRep, nAtom = np.shape(R)
    nuclDens = np.zeros(nRep)

    for I in range(nRep):
        RIv = R[I, 0]
        allGauss = [gaussian(RIv, RJv, sigma[I, 0])
                    for RJv in R[:, 0]]
        nuclDens[I] = np.mean(allGauss)

    return nuclDens, R[:, 0]


def calc_nucl_dens(RIv, v, ctmqc_env):
    """
    Will calculate the nuclear density on replica I (for 1 atom)

    Inputs:
        * I => <int> Replica Index I
        * v => <int> Atom index v
        * sigma => the width of the gaussian on replica J atom v
        * ctmqc_env => the ctmqc environment
    """
    RJv = ctmqc_env['pos'][:, v]
    sigma = ctmqc_env['sigma'][:, v]
    allGauss = [gaussian(RIv, RJ, sig) for sig, RJ in zip(sigma, RJv)]
    return np.mean(allGauss)


def calc_sigma(ctmqc_env, I, v):
    """
    Will calculate the value of sigma used in constructing the nuclear density.

    This algorithm doesn't seem to work -it gives discontinuous sigma and sigma
    seems to blow up. To fix discontinuities a weighted stddev might work.
    """
    cnst = ctmqc_env['const']
    sigma_tm = ctmqc_env['sigma_tm'][I, v]
    cutoff_rad = cnst * sigma_tm
    sig_thresh = cnst/ctmqc_env['nrep'] * np.min(ctmqc_env['sigma_tm'])

    distances = ctmqc_env['pos'] - ctmqc_env['pos'][I, v]
    new_var = np.std(distances[distances < cutoff_rad])

    if new_var < sig_thresh:
        new_var = sig_thresh
    ctmqc_env['sigma'][I, v] = new_var


def calc_QM_FD(ctmqc_env, I, v):
    """
    Will calculate the quantum momentum (only for 1 atom currently)
    """
    if ctmqc_env['do_sigma_calc']:
        calc_sigma(ctmqc_env, I, v)
    RIv = ctmqc_env['pos'][I, v]
    dx = ctmqc_env['dx']

    nuclDens_xm = calc_nucl_dens(RIv - dx, v, ctmqc_env)
    nuclDens = calc_nucl_dens(RIv, v, ctmqc_env)
    nuclDens_xp = calc_nucl_dens(RIv + dx, v, ctmqc_env)

    gradNuclDens = np.gradient([nuclDens_xm, nuclDens, nuclDens_xp],
                               dx)[2]
    if nuclDens < 1e-12:
        return 0

    QM = -gradNuclDens/(2*nuclDens)

    return QM / ctmqc_env['mass'][v]


def calc_QM_analytic(ctmqc_env, I, v):
    """
    Will use the analytic formula provided in SI to calculate the QM.
    """
    if ctmqc_env['do_sigma_calc']:
        calc_sigma(ctmqc_env, I, v)
    RIv = ctmqc_env['pos'][I, v]
    WIJ = np.zeros(ctmqc_env['nrep'])  # Only calc WIJ for rep I
    allGauss = [gaussian(RIv, RJv, sig)
                for (RJv, sig) in zip(ctmqc_env['pos'][:, v],
                                      ctmqc_env['sigma'][:, v])]
    QM = 0.0
    # Calc WIJ
    sigma2 = ctmqc_env['sigma'][:, v]**2
    WIJ = allGauss / (2. * sigma2 * np.sum(allGauss))

    # Calc QM
    QM = np.sum(WIJ * (RIv - ctmqc_env['pos'][:, v]))

    return QM / ctmqc_env['mass'][v]


def calc_all_alpha(ctmqc_env, v):
    """
    Will calculate alpha for all replicas (and 1 atom)
    """
    nRep = ctmqc_env['nrep']
    alpha = np.zeros(nRep)
    for I in range(nRep):
        RIv = ctmqc_env['pos'][I, v]
        WIJ = np.zeros(ctmqc_env['nrep'])  # Only calc WIJ for rep I
        allGauss = [gaussian(RIv, RJv, sig)
                    for (RJv, sig) in zip(ctmqc_env['pos'][:, v],
                                          ctmqc_env['sigma'][:, v])]
        # Calc WIJ and alpha
        sigma2 = ctmqc_env['sigma'][:, v]**2
        WIJ = allGauss / (2. * sigma2 * np.sum(allGauss))
        alpha[I] = np.sum(WIJ)
    return alpha


def calc_Qlk(ctmqc_env, I, v):
    """
    Will return an array of size (Nstate, Nstate) containing data for the
    Quantum Momentum with the pairwise states.

    N.B Currently only works for a 2 state system
    """
#    print("Qlk only works for 2 states atm")
#    if ctmqc_env['do_sigma_calc']:
#        calc_sigma(ctmqc_env, I, v)
#    nState, nRep = ctmqc_env['nstate'], ctmqc_env['nrep']
#    Qlk = 0.0
#
#    # First Calculate Alpha
#    RIv = ctmqc_env['pos'][:, v]
#    alpha = calc_all_alpha( ctmqc_env, v)
#
#    # Then Calculate Rlk
#    C2 = ctmqc_env['adPops'][:, v, :]
#    f = ctmqc_env['adMom'][:, v, :]
#    bottom_Rlk = C2[:, 0] * C2[:, 1] * (f[:, 0] - f[:, 1])
#    nonZeroMask = bottom_Rlk > 0.0
#    print(ctmqc_env['adMom'])
#
#    return Qlk
    pass


def test_QM_calc(ctmqc_env):
    """
    Will compare the output of the analytic QM calculation and the finite
    difference one.
    """
    allDiffs, allPos = [], []
    for I in range(ctmqc_env['nrep']):
        for v in range(ctmqc_env['natom']):
            QM1 = calc_QM_analytic(ctmqc_env, I, v)
            QM2 = calc_QM_FD(ctmqc_env, I, v)
            diff = 100*(QM1 - QM2) / QM2

            allDiffs.append(diff)
            allPos.append(ctmqc_env['pos'][I, v])

    if np.max(np.abs(allDiffs)) > 0.1:
        raise SystemExit("Analytic Quantum Momentum != Finite Difference")

    print("Avg Percentage Diff = %.2g%% +/- %.2g" % (np.mean(allDiffs),
                                                     np.std(allDiffs)))
    print("Max Percentage Diff = %.2g%%" % np.max(np.abs(allDiffs)))
    print("Min Percentage Diff = %.2g%%" % np.min(np.abs(allDiffs)))
    return allDiffs, allPos


test_norm_gauss()
