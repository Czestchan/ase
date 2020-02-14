import time
import copy
from scipy.spatial.distance import euclidean
from ase.optimize.activelearning.gp.calculator import GPCalculator
from ase.parallel import parprint, parallel_function
from ase.optimize import QuasiNewton
from ase.optimize.activelearning.io import get_fmax, TrainingSet


class AIDMin:

    def __init__(self, atoms, model_calculator=None, force_consistent=None,
                 max_train_data=5, optimizer=QuasiNewton,
                 max_train_data_strategy='nearest_observations',
                 geometry_threshold=0.001, trajectory='AID.traj',
                 use_previous_observations=False,
                 trainingset='AID_observations.traj'):
        """
        Artificial Intelligence-Driven energy Minimizer (AID-Min) algorithm.
        Optimize atomic structure using a surrogate machine learning
        model [1,2]. Atomic positions, potential energies and forces
        information are used to build a modelled potential energy surface (
        PES) that can be optimized to obtain new suggested structures
        towards finding a local minima in the targeted PES.

        [1] E. Garijo del Rio, J. J. Mortensen and K. W. Jacobsen.
        Phys. Rev. B 100, 104103 (2019).
        https://journals.aps.org/prb/abstract/10.1103/PhysRevB.100.104103
        [2] J. A. Garrido Torres, E. Garijo del Rio, A. H. Larsen,
        V. Streibel, J. J. Mortensen, M. Bajdich, F. Abild-Pedersen,
        K. W. Jacobsen, T. Bligaard. (submitted).

        Parameters
        --------------
        atoms: Atoms object
            The Atoms object to relax.

        model_calculator: Model object.
            Model calculator to be used for predicting the potential energy
            surface. The default is None which uses a GP model with the Squared
            Exponential Kernel and other default parameters. See
            *ase.optimize.activelearning.gp.calculator* GPModel for default GP
            parameters.

        force_consistent: boolean or None
            Use force-consistent energy calls (as opposed to the energy
            extrapolated to 0 K). By default (force_consistent=None) uses
            force-consistent energies if available in the calculator, but
            falls back on force_consistent=False if not.

                trajectory: string
            Filename to store the predicted optimization.
                Additional information:
                - Uncertainty: The energy uncertainty in each image can be
                  accessed in image.info['uncertainty'].

        use_previous_observations: boolean
            If False. The optimization starts from scratch.
                If the observations were saved to a trajectory file,
                it is overwritten. If they were kept in a list, they are
                deleted.
            If True. The optimization uses the information that was already
                in the training set that is provided in the optimization.

        trainingset: None, trajectory file or list
            Where the training set is kept, either saved to disk in a
            trajectory file or kept in memory in a list.
            options:
                None (default):
                    A trajectory file named *trajectory*_observations.traj is
                    automatically generated and the training set is saved to
                    it while generated.
                str: trajectory filename where to append the training set
                list: list were to append the atoms objects.
        """

        # Model calculator:
        self.model_calculator = model_calculator
        # Default GP Calculator parameters if not specified by the user.
        if model_calculator is None:
            self.model_calculator = GPCalculator(
                               train_images=[], scale=0.3, weight=2.,
                               noise=0.003, update_prior_strategy='fit',
                               max_train_data_strategy=max_train_data_strategy,
                               max_train_data=max_train_data,
                               calculate_uncertainty=False)

        # Active Learning setup (single-point calculations).
        self.function_calls = 0
        self.force_calls = 0
        self.step = 0

        self.atoms = atoms
        self.constraints = atoms.constraints
        self.ase_calc = atoms.get_calculator()
        self.optimizer = optimizer

        self.fc = force_consistent
        self.trajectory = trajectory
        self.geometry_threshold = geometry_threshold

        # Initialize training set

        if trainingset is None:
            trajectory_main = self.trajectory.split('.')[0]
            self.train = TrainingSet(
                trajectory_main + '_observations.traj',
                use_previous_observations=False
                )
        else:
            self.train = TrainingSet(
                        trainingset,
                        use_previous_observations=use_previous_observations
                        )
        # Initialize a trajectory file for the current optimization.
        self.traj = TrainingSet(self.trajectory,
                                use_previous_observations=False
                                )


        self.atoms.get_potential_energy()
        self.atoms.get_forces()

        self.train.dump(atoms=self.atoms, method='min')
        self.traj.dump(atoms=self.atoms, method='min')

    def run(self, fmax=0.05, ml_steps=500, steps=200):

        """
        Executing run will start the optimization process.

        Parameters
        ----------
        fmax : float
            Convergence criteria (in eV/Angstrom).

        ml_steps: int
            Maximum number of steps for the optimization on the modelled
            potential energy surface.

        steps: int
            Maximum number of steps for the surrogate.

        Returns
        -------
        Optimized structure. The optimization process can be followed in
        *trajectory_observations.traj*.

        """
        self.fmax = fmax
        self.steps = steps

        # Always start from 'atoms' positions.
        starting_atoms = self.train.load_last()
        starting_atoms.positions = copy.deepcopy(self.atoms.positions)

        while not self.fmax >= get_fmax(self.atoms):

            # 1. Gather observations in every iteration.
            # This serves to use the previous observations (useful for
            # continuing calculations and/or for parallel runs).
            train_images = self.train.load_set()

            # Update constraints in case they have changed from previous runs.
            for img in train_images:
                img.set_constraint(self.constraints)

            # 2. Update model calculator.
            ml_converged = False
            surrogate_positions = self.atoms.positions

            # Probed positions are used for low-memory.
            probed_atoms = [copy.deepcopy(starting_atoms)]
            probed_atoms[0].positions = copy.deepcopy(self.atoms.positions)

            while not ml_converged:
                gp_calc = copy.deepcopy(self.model_calculator)
                gp_calc.update_train_data(train_images=train_images,
                                          test_images=probed_atoms)
                self.atoms.set_calculator(gp_calc)

                # 3. Optimize the structure in the predicted PES.
                ml_opt = self.optimizer(self.atoms,
                                        logfile=None, trajectory=None)
                ml_opt.run(fmax=(fmax * 0.01), steps=ml_steps)
                surrogate_positions = self.atoms.positions

                if len(probed_atoms) >= 2:
                    l1_probed_pos = probed_atoms[-1].positions.reshape(-1)
                    l2_probed_pos = probed_atoms[-2].positions.reshape(-1)
                    dl1l2 = euclidean(l1_probed_pos, l2_probed_pos)
                    if dl1l2 <= self.geometry_threshold:
                        ml_converged = True

                probed = copy.deepcopy(probed_atoms[0])
                probed.positions = copy.deepcopy(self.atoms.positions)
                probed_atoms += [probed]

            # Update step (this allows to stop algorithm before evaluating).
            if self.step >= self.steps:
                break

            # 4. Evaluate the target function and save it in *observations*.
            self.atoms.positions = surrogate_positions
            self.atoms.set_calculator(self.ase_calc)
            self.atoms.get_potential_energy(force_consistent=self.fc)
            self.atoms.get_forces()

            self.train.dump(atoms=self.atoms, method='min')
            self.traj.dump(atoms=self.atoms, method='min')

            self.function_calls = len(train_images) + 1
            self.force_calls = self.function_calls
            self.step += 1

            # 5. Print simple output.
            parprint("-" * 26)
            parprint('Step:', self.step)
            parprint('Function calls:', self.function_calls)
            parprint('Time:', time.strftime("%m/%d/%Y, %H:%M:%S",
                                            time.localtime()))
            parprint('Energy:', self.atoms.get_potential_energy(self.fc))
            parprint("fmax:", get_fmax(self.atoms))
            parprint("-" * 26 + "\n")
        print_cite_aidmin()

@parallel_function
def print_cite_aidmin():
    msg = "\n" + "-" * 79 + "\n"
    msg += "You are using AIDMin. Please cite: \n"
    msg += "[1] E. Garijo del Rio, J. J. Mortensen and K. W. Jacobsen. "
    msg += "Phys. Rev. B 100, 104103."
    msg += "https://doi.org/10.1103/PhysRevB.100.104103. \n"
    msg += "[2] J. A. Garrido Torres, E. Garijo del Rio, V. Streibel "
    msg += "T. S. Choski, J. J. Mortensen, A. Urban, M. Bajdich "
    msg += "F. Abild-Pedersen, K. W. Jacobsen, and T. Bligaard. Submitted. \n"
    msg += "-" * 79 + '\n'
    parprint(msg)
